# Local Testing

This setup runs the web app and Electron app against a local SQLite database.
It does not use the production Azure SQL database.

## Web App

From PowerShell:

```powershell
cd C:\Users\Jackson\Documents\Projects\tmc-time-tracker\tmc-time-tracker\flask_app
.\start-dev.ps1
```

Open:

```text
http://localhost:5000/dev-login
```

The dev login only works when:

```text
APP_ENV=development
ENABLE_DEV_LOGIN=1
```

## Electron App

Start the web app first. Then open a second PowerShell:

```powershell
cd C:\Users\Jackson\Documents\Projects\tmc-time-tracker\tmc-time-tracker\electron_app
.\start-dev.ps1
```

The Electron app reads `electron_app\.env` and uses:

```text
FLASK_API_BASE_URL=http://localhost:5000
DEV_AUTH_BYPASS=1
```

## Reset Local Test Data

Stop the web app, then delete:

```text
C:\Users\Jackson\Documents\Projects\tmc-time-tracker\tmc-time-tracker\flask_app\instance\dev_time_tracker.db
```

Run `.\start-dev.ps1` again to recreate the database and local admin user.
