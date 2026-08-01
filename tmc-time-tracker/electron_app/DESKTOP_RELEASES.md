# Desktop release process

## Version 2.7.6

- Starts time tracking automatically after meaningful local user input inside the employee schedule.
- Adds per-employee working hours and working days to the admin area.
- Uses server time for clock-out and separates device liveness from verified user input.
- Flags mouse-movement-only patterns for integrity review while storing no key content.
- Keeps the activity fallback for clients up to version 2.7.5 during the rollout.

## Version and file name

- Continue the semantic patch sequence from the latest version.
- The installer name must remain `Time-tracker-Setup-X.Y.Z.exe`.
- Never overwrite an installer that already exists under a different version.

## Build a test candidate

From `electron_app` run:

```powershell
.\scripts\build-release.ps1
```

The command builds in a fresh timestamped `dist\build-X.Y.Z-*` directory so a
running older test build cannot lock the next release. It creates
`dist\candidate-X.Y.Z` with the installer, blockmap, `latest.yml`, and a
SHA-256 release manifest.

## Azure release channels

1. Upload a new, unapproved installer to `xperttimer/test-files` only.
2. Test installation, Windows SSO, manual account switching, sign-out, tracking,
   and web dashboard access.
3. After explicit approval, upload the exact tested installer, blockmap, and
   `latest.yml` to `xperttimer/updates` and keep a versioned installer in the
   container root as a download archive.
4. Keep at least the three newest versioned installers. Never delete or replace
   one of those three during a release.

Updating `updates/latest.yml` is the production rollout step. It must not happen
while a version is still being tested.
