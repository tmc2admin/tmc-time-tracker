// auth.js
const { BrowserWindow } = require('electron');
const axios = require('axios');
const crypto = require('crypto');
const log = require('electron-log');
const config = require('./config'); 

// --- Configuration ---
const MS_AUTHORITY = config.OAUTH_AUTHORITY;
const MS_CLIENT_ID = config.OAUTH_CLIENT_ID;
const MS_SCOPE = 'openid profile email User.Read';
const REDIRECT_URI = 'http://localhost:8081/auth/callback';
const ALLOWED_DOMAIN = '@tm-connect.de';

// --- PKCE Helper Functions ---
function generateCodeVerifier() {
    return crypto.randomBytes(32).toString('base64url');
}

function generateCodeChallenge(verifier) {
    return crypto.createHash('sha256').update(verifier).digest('base64url');
}

/**
 * Initiates the Microsoft OAuth login flow within a dedicated Electron window.
 * @returns {Promise<object>} A promise that resolves with the user's profile from Microsoft Graph.
 */
function authenticateWithMicrosoft() {
    return new Promise(async (resolve, reject) => {
        // Validate Config Loaded
        if (!MS_CLIENT_ID || !MS_AUTHORITY) {
            const configError = new Error('Microsoft OAuth configuration is missing (Check config.js).');
            log.error(`[Auth] ${configError.message}`);
            return reject(configError);
        }

        const codeVerifier = generateCodeVerifier();
        const codeChallenge = generateCodeChallenge(codeVerifier);

        const authorizeUrl = new URL(`${MS_AUTHORITY}/oauth2/v2.0/authorize`);
        authorizeUrl.searchParams.append('client_id', MS_CLIENT_ID);
        authorizeUrl.searchParams.append('response_type', 'code');
        authorizeUrl.searchParams.append('redirect_uri', REDIRECT_URI);
        authorizeUrl.searchParams.append('scope', MS_SCOPE);
        authorizeUrl.searchParams.append('response_mode', 'query');
        
        // This parameter forces the "Pick an account" screen seamlessly
        // without needing to wipe the user's saved cookies!
        authorizeUrl.searchParams.append('prompt', 'select_account');
        authorizeUrl.searchParams.append('code_challenge', codeChallenge);
        authorizeUrl.searchParams.append('code_challenge_method', 'S256');
        
        let authCompleted = false;
        let authWindow = new BrowserWindow({
            width: 600,
            height: 800,
            modal: true,
            show: false,
            autoHideMenuBar: true,
            webPreferences: {
                nodeIntegration: false,
                contextIsolation: true,
                sandbox: true,
            },
        });

        // Load the Microsoft Login Page
        authWindow.loadURL(authorizeUrl.toString());
        
        authWindow.once('ready-to-show', () => {
            if (authWindow) authWindow.show();
        });
        
        log.info('[Auth] Opened authentication window.');

        const { webContents } = authWindow;
        
        webContents.on('will-redirect', async (event, newUrl) => {
            if (newUrl.startsWith(REDIRECT_URI)) {
                event.preventDefault();
                authCompleted = true; 

                const url = new URL(newUrl);
                const authCode = url.searchParams.get('code');
                const error = url.searchParams.get('error');

                if (error) {
                     log.error(`[Auth] Microsoft returned error: ${error}`);
                     // We just reject here; the finally{} block below handles closing the window
                     return reject(new Error(`Microsoft Auth Error: ${error}`));
                }

                if (!authCode) {
                    const authError = new Error('Authorization code not found.');
                    log.error(`[Auth] ${authError.message}`);
                    return reject(authError);
                }

                log.info('[Auth] Authorization code received. Exchanging for tokens...');
                if (authWindow) authWindow.hide();

                try {
                    const tokenResponse = await axios.post(
                        `${MS_AUTHORITY}/oauth2/v2.0/token`,
                        new URLSearchParams({
                            client_id: MS_CLIENT_ID,
                            scope: MS_SCOPE,
                            code: authCode,
                            redirect_uri: REDIRECT_URI,
                            grant_type: 'authorization_code',
                            code_verifier: codeVerifier,
                        })
                    );

                    const tokens = tokenResponse.data;
                    log.info('[Auth] Tokens received.');

                    const userInfoResponse = await axios.get('https://graph.microsoft.com/v1.0/me', {
                        headers: { 'Authorization': `Bearer ${tokens.access_token}` },
                    });
                    
                    const userProfile = userInfoResponse.data;
                    const userEmail = userProfile.mail || userProfile.userPrincipalName;

                    if (!userEmail || !userEmail.toLowerCase().endsWith(ALLOWED_DOMAIN)) {
                        throw new Error(`Access denied. Only ${ALLOWED_DOMAIN} accounts are allowed.`);
                    }

                    log.info(`[Auth] User validated: ${userEmail}`);
                    resolve({ userProfile });

                } catch (error) {
                    const msg = error.response?.data?.error_description || error.message;
                    log.error(`[Auth] Token exchange failed: ${msg}`);
                    reject(new Error(msg));
                } finally {
                    // Safe cleanup: Only close the window here to avoid race conditions
                    if (authWindow && !authWindow.isDestroyed()) {
                         authWindow.close();
                    }
                }
            }
        });

        authWindow.on('closed', () => {
            authWindow = null;
            if (!authCompleted) {
                reject({ isUserCancellation: true, message: 'User closed the login window.' });
            }
        });
    });
}

module.exports = { authenticateWithMicrosoft };