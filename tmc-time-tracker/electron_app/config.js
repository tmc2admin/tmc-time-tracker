// config.js
const path = require('path');
const fs = require('fs');
const { app } = require('electron');
const dotenv = require('dotenv');

// 1. Try to load .env (Only works in Dev or if file exists)
// We do this logic manually to ensure it works before the app is fully ready
const isPackaged = app.isPackaged;
const envPath = isPackaged 
    ? path.join(process.resourcesPath, '.env') 
    : path.join(__dirname, '.env');

if (fs.existsSync(envPath)) {
    dotenv.config({ path: envPath });
}

// 2. Export Configuration
// Priority: 1. Environment Variable (.env) -> 2. Hardcoded Production Value
module.exports = {
    // API Configuration
    FLASK_API_BASE_URL: process.env.FLASK_API_BASE_URL || 'https://tmc-time-tracker-a8aba8cxfpdwfseq.westeurope-01.azurewebsites.net', 
    
    // Microsoft OAuth Configuration
    OAUTH_AUTHORITY: process.env.OAUTH_AUTHORITY || 'https://login.microsoftonline.com/c753d4f6-12e5-4eae-a4a5-07b42cb19290',
    OAUTH_CLIENT_ID: process.env.OAUTH_CLIENT_ID || '29a2d157-e5eb-4905-acc2-5d664e040a9e', 
    OAUTH_CLIENT_SECRET: process.env.OAUTH_CLIENT_SECRET || '', 
    OAUTH_REDIRECT_URI: process.env.OAUTH_REDIRECT_URI || '29a2d157-e5eb-4905-acc2-5d664e040a9e', 

    GH_TOKEN: process.env.GH_TOKEN || '', 
    DEV_AUTH_BYPASS: process.env.DEV_AUTH_BYPASS === '1',
    DEV_AUTH_EMAIL: process.env.DEV_AUTH_EMAIL || 'dev.admin@tm-connect.de',
    DEV_AUTH_NAME: process.env.DEV_AUTH_NAME || 'Local Test Admin',
    DEV_AUTH_OID: process.env.DEV_AUTH_OID || 'local-dev-admin',
    
    // Feature Flags & Settings
    IS_DEV: !isPackaged,
    APP_VERSION: app.getVersion()
};
