const { BrowserWindow, shell } = require('electron');
const axios = require('axios');
const crypto = require('crypto');
const log = require('electron-log');
const { PublicClientApplication } = require('@azure/msal-node');
const config = require('./config');

const MS_AUTHORITY = config.OAUTH_AUTHORITY;
const MS_CLIENT_ID = config.OAUTH_CLIENT_ID;
const MS_SCOPES = ['User.Read'];
const REDIRECT_URI = config.OAUTH_REDIRECT_URI;
const ALLOWED_DOMAIN = '@tm-connect.de';

let brokerClient = null;
let brokerAccount = null;

function generateCodeVerifier() {
    return crypto.randomBytes(32).toString('base64url');
}

function generateCodeChallenge(verifier) {
    return crypto.createHash('sha256').update(verifier).digest('base64url');
}

function isUserCancellation(error) {
    const code = String(error?.errorCode || error?.code || '').toLowerCase();
    const message = String(error?.message || '').toLowerCase();
    return code.includes('user_canceled') || message.includes('user canceled') || message.includes('user cancelled');
}

function asCancellation(error) {
    return {
        isUserCancellation: true,
        message: error?.message || 'User canceled authentication.'
    };
}

function getWindowHandle(parentWindow) {
    if (!parentWindow || parentWindow.isDestroyed()) return Buffer.from([0]);
    return parentWindow.getNativeWindowHandle();
}

function getBrokerClient() {
    if (process.platform !== 'win32') return null;
    if (brokerClient) return brokerClient;

    try {
        const { NativeBrokerPlugin } = require('@azure/msal-node-extensions');
        const nativeBrokerPlugin = new NativeBrokerPlugin();
        if (!nativeBrokerPlugin.isBrokerAvailable) {
            log.warn('[Auth] Windows native broker is unavailable.');
            return null;
        }

        brokerClient = new PublicClientApplication({
            auth: {
                clientId: MS_CLIENT_ID,
                authority: MS_AUTHORITY
            },
            broker: { nativeBrokerPlugin }
        });
        return brokerClient;
    } catch (error) {
        log.warn(`[Auth] Could not initialize Windows native broker: ${error.message}`);
        return null;
    }
}

async function fetchAndValidateUserProfile(accessToken) {
    const response = await axios.get('https://graph.microsoft.com/v1.0/me', {
        headers: { Authorization: `Bearer ${accessToken}` }
    });
    const userProfile = response.data;
    const userEmail = userProfile.mail || userProfile.userPrincipalName;

    if (!userEmail || !userEmail.toLowerCase().endsWith(ALLOWED_DOMAIN)) {
        throw new Error(`Access denied. Only ${ALLOWED_DOMAIN} accounts are allowed.`);
    }

    return userProfile;
}

async function authenticateWithWindowsBroker(parentWindow, selectAccount) {
    const client = getBrokerClient();
    if (!client) return null;

    const baseRequest = {
        scopes: MS_SCOPES,
        windowHandle: getWindowHandle(parentWindow),
        openBrowser: async (url) => shell.openExternal(url)
    };

    let authResult;
    if (selectAccount) {
        authResult = await client.acquireTokenInteractive({
            ...baseRequest,
            prompt: 'select_account'
        });
    } else {
        try {
            authResult = await client.acquireTokenInteractive({
                ...baseRequest,
                prompt: 'none'
            });
            log.info('[Auth] Windows SSO completed silently.');
        } catch (silentError) {
            log.info(`[Auth] Silent Windows SSO needs interaction: ${silentError.errorCode || silentError.message}`);
            authResult = await client.acquireTokenInteractive(baseRequest);
        }
    }

    brokerAccount = authResult.account || null;
    const userProfile = await fetchAndValidateUserProfile(authResult.accessToken);
    log.info(`[Auth] Windows broker validated user: ${userProfile.mail || userProfile.userPrincipalName}`);
    return { userProfile, authMethod: 'windows-broker' };
}

function authenticateWithWebWindow(parentWindow, selectAccount) {
    return new Promise((resolve, reject) => {
        const codeVerifier = generateCodeVerifier();
        const codeChallenge = generateCodeChallenge(codeVerifier);
        const authorizeUrl = new URL(`${MS_AUTHORITY}/oauth2/v2.0/authorize`);
        authorizeUrl.searchParams.append('client_id', MS_CLIENT_ID);
        authorizeUrl.searchParams.append('response_type', 'code');
        authorizeUrl.searchParams.append('redirect_uri', REDIRECT_URI);
        authorizeUrl.searchParams.append('scope', `openid profile email ${MS_SCOPES.join(' ')}`);
        authorizeUrl.searchParams.append('response_mode', 'query');
        authorizeUrl.searchParams.append('code_challenge', codeChallenge);
        authorizeUrl.searchParams.append('code_challenge_method', 'S256');
        if (selectAccount) authorizeUrl.searchParams.append('prompt', 'select_account');

        let authCompleted = false;
        let authWindow = new BrowserWindow({
            width: 600,
            height: 800,
            parent: parentWindow && !parentWindow.isDestroyed() ? parentWindow : undefined,
            modal: Boolean(parentWindow && !parentWindow.isDestroyed()),
            show: false,
            autoHideMenuBar: true,
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true
            }
        });

        authWindow.loadURL(authorizeUrl.toString());
        authWindow.once('ready-to-show', () => authWindow?.show());
        log.info('[Auth] Opened web authentication fallback.');

        authWindow.webContents.on('will-redirect', async (event, newUrl) => {
            if (!newUrl.startsWith(REDIRECT_URI)) return;

            event.preventDefault();
            authCompleted = true;
            const url = new URL(newUrl);
            const authCode = url.searchParams.get('code');
            const authError = url.searchParams.get('error');

            if (authError) {
                reject(new Error(`Microsoft Auth Error: ${authError}`));
                authWindow?.close();
                return;
            }
            if (!authCode) {
                reject(new Error('Authorization code not found.'));
                authWindow?.close();
                return;
            }

            authWindow?.hide();
            try {
                const tokenResponse = await axios.post(
                    `${MS_AUTHORITY}/oauth2/v2.0/token`,
                    new URLSearchParams({
                        client_id: MS_CLIENT_ID,
                        scope: `openid profile email ${MS_SCOPES.join(' ')}`,
                        code: authCode,
                        redirect_uri: REDIRECT_URI,
                        grant_type: 'authorization_code',
                        code_verifier: codeVerifier
                    })
                );
                const userProfile = await fetchAndValidateUserProfile(tokenResponse.data.access_token);
                resolve({ userProfile, authMethod: 'web-fallback' });
            } catch (error) {
                const message = error.response?.data?.error_description || error.message;
                reject(new Error(message));
            } finally {
                authWindow?.close();
            }
        });

        authWindow.on('closed', () => {
            authWindow = null;
            if (!authCompleted) reject(asCancellation());
        });
    });
}

async function authenticateWithMicrosoft({ parentWindow = null, selectAccount = false } = {}) {
    if (config.DEV_AUTH_BYPASS && config.IS_DEV) {
        log.warn('[Auth] DEV_AUTH_BYPASS is enabled. Using local test identity.');
        return {
            userProfile: {
                id: config.DEV_AUTH_OID,
                displayName: config.DEV_AUTH_NAME,
                mail: config.DEV_AUTH_EMAIL,
                userPrincipalName: config.DEV_AUTH_EMAIL
            },
            authMethod: 'development-bypass'
        };
    }

    if (!MS_CLIENT_ID || !MS_AUTHORITY) {
        throw new Error('Microsoft OAuth configuration is missing (check config.js).');
    }

    try {
        const brokerResult = await authenticateWithWindowsBroker(parentWindow, selectAccount);
        if (brokerResult) return brokerResult;
    } catch (error) {
        if (isUserCancellation(error)) throw asCancellation(error);
        log.warn(`[Auth] Windows broker failed; using web fallback: ${error.errorCode || error.message}`);
    }

    return authenticateWithWebWindow(parentWindow, selectAccount);
}

async function signOutMicrosoft() {
    const client = getBrokerClient();
    if (client && brokerAccount) {
        try {
            await client.signOut({ account: brokerAccount });
        } catch (error) {
            log.warn(`[Auth] Broker sign-out could not be completed: ${error.errorCode || error.message}`);
        }
    }
    brokerAccount = null;
}

module.exports = { authenticateWithMicrosoft, signOutMicrosoft };
