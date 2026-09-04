# Smart Bandage — mobile

React Native (Expo) counterpart to the Phase 5 dashboard (`../frontend`),
against the same Phase 4 backend (`../backend`). Three screens (sign in,
device list, live monitor), no navigation library -- see `App.tsx`.

## Getting started

```bash
cd mobile
npm install
npm run web        # runs in a browser via react-native-web -- fastest loop
```

For a physical device or emulator, `localhost` won't reach your dev
machine -- point the app at your machine's LAN IP first:

```bash
EXPO_PUBLIC_API_BASE_URL=http://<your-lan-ip>:8000 \
EXPO_PUBLIC_WS_BASE_URL=ws://<your-lan-ip>:8000 \
npm start           # scan the QR code with Expo Go
```

Whichever origin you run from, add it to the backend's `CORS_ORIGINS`
(see `../backend/app/config.py`, or `CORS_ORIGINS` in `../docker-compose.yml`
if running the backend via Docker) or sign-in will fail with "Could not
reach the backend".

Sign in with the seed login (`admin` / `admin`), pick a device, then start
a simulation from the dashboard (or `python ../scripts/seed_demo_data.py`)
to see live readings and alerts stream in over the WebSocket.

## Layout

```
App.tsx              screen switcher (login / devices / monitor) -- no nav lib
src/config.ts         API_BASE / WS_BASE
src/api.ts             REST client -- mirrors frontend/src/api.ts
src/types.ts            response shapes -- mirrors frontend/src/types.ts
src/hooks/useDeviceSocket.ts  WS /ws/devices/{id} -- ported from the dashboard's hook
src/screens/          LoginScreen, DevicesScreen, MonitorScreen
eas.json              EAS Build profiles (development / preview / production)
app.json              bundle identifiers, EAS project id -- see below
```

## Building for real devices

`npm run web` / `npm start` (Expo Go) are dev-loop only -- they never
produce an installable binary. Getting onto TestFlight / Play internal
testing goes through [EAS Build](https://docs.expo.dev/build/introduction/),
Expo's hosted build service. This repo ships the config (`app.json`,
`eas.json`); the steps below are the operational part that needs a real
Expo account and can't be done from this environment.

1. **One-time setup**
   ```bash
   npx eas-cli login              # or: npm install -g eas-cli && eas login
   npx eas-cli init                # links this app to a project in your Expo account,
                                    # writes the real projectId into app.json's extra.eas
   ```
   Then replace the placeholders `app.json` was scaffolded with:
   - `expo.owner` -- your Expo account/org username
   - `expo.ios.bundleIdentifier` / `expo.android.package` -- your own
     reverse-DNS app id if `com.smartbandage.mobile` isn't yours to publish under
   - the `REPLACE_WITH_*_BACKEND_URL` values in `eas.json` -- point `preview`
     at a staging deploy of `../backend` and `production` at the real one
     (see the root README's "Ship what already works" section for getting
     the backend + TLS deployed first). Use `wss://` for the WS URL once
     the backend is behind the TLS overlay, not `ws://`.

2. **Build**
   ```bash
   npm run build:preview            # internal-distribution build (APK / ad-hoc IPA)
   # or
   npm run build:production         # store-ready build, auto-incremented version
   ```
   EAS builds remotely and hands back a QR code / download link -- no Xcode
   or Android Studio needed locally, including for the iOS build.

3. **Distribute**
   ```bash
   npm run submit:android           # uploads the production build to Play Console
   npm run submit:ios               # uploads it to App Store Connect
   ```
   First run of `eas submit` walks you through connecting the relevant
   store account; internal testers can also just install the `preview`
   build's APK / ad-hoc IPA directly from the link EAS prints, no store
   review needed.

None of this has been run end to end yet -- there's no Expo account, Apple
Developer Program membership, or Google Play Console access in the
environment that wrote this config. What's verified is that `app.json` /
`eas.json` are well-formed and `npm run typecheck` passes against them
(also run in CI, see `../.github/workflows/ci.yml`).
