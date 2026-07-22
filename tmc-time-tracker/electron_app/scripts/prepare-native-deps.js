const fs = require('fs');
const path = require('path');

if (process.platform !== 'win32') process.exit(0);

const extensionsPackage = require.resolve('@azure/msal-node-extensions/package.json');
const runtimePackage = require.resolve('@azure/msal-node-runtime/package.json', {
    paths: [path.dirname(extensionsPackage)]
});
const runtimeDir = path.dirname(runtimePackage);

require(path.join(runtimeDir, 'copyBinaries.js'));

const runtimeBinary = path.join(runtimeDir, 'dist', 'msal-node-runtime.node');
const runtimeLibrary = path.join(runtimeDir, 'dist', 'msalruntime.dll');
if (!fs.existsSync(runtimeBinary) || !fs.existsSync(runtimeLibrary)) {
    throw new Error('Microsoft Windows broker binaries could not be prepared.');
}

const requiredNativeFiles = [
    path.join(__dirname, '..', 'node_modules', 'active-win', 'lib', 'binding', 'napi-9-win32-unknown-x64', 'node-active-win.node'),
    path.join(__dirname, '..', 'node_modules', 'keytar', 'build', 'Release', 'keytar.node'),
    path.join(__dirname, '..', 'node_modules', 'uiohook-napi', 'prebuilds', 'win32-x64', 'node.napi.node')
];

for (const nativeFile of requiredNativeFiles) {
    if (!fs.existsSync(nativeFile)) {
        throw new Error(`Required Windows native dependency is missing: ${nativeFile}`);
    }
}

console.log('Windows broker and native dependencies are ready.');
