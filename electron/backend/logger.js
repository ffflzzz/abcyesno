const fs = require('fs');
const path = require('path');
const os = require('os');

// Keep all portable runtime logs under the isolated user data directory.
const portableDataDir = path.join(os.homedir(), '.hermes_portable_data');
const logDir = path.join(portableDataDir, 'logs');
try {
  fs.mkdirSync(logDir, { recursive: true });
} catch (_) {}

const logFile = path.join(logDir, 'electron.log');

function log(level, message) {
  const line = `[${new Date().toISOString()}] [${level}] ${message}\n`;
  try {
    fs.appendFileSync(logFile, line);
  } catch (_) {
    // ignore logging failures
  }
}

module.exports = { log, logFile, logDir };
