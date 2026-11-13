# Google Play asset provenance

All files in `assets/` are derived from the existing app branding or genuine Android captures. No interface elements or racing values were generated.

| File | Source | Use |
| --- | --- | --- |
| `play-icon-512.png` | Android `mipmap-xxxhdpi/ic_launcher.png`, resized to 512 × 512 | Store icon candidate |
| `feature-graphic-1024x500.png` | `distribution/website/assets/pit-box-race-card-banner.png`, resized with dark top and bottom margins | Feature graphic candidate |
| `phone-01-race-plans.png` | Owner's 13 November 2026 Android phone capture, Strategy tab, app 5.7.1 | Phone screenshot candidate |
| `phone-02-setup-lab.png` | Owner's 13 November 2026 Android phone capture, Setup Lab, app 5.7.1 | Phone screenshot candidate |
| `phone-03-driver-dashboard.png` | Owner's 13 November 2026 Android phone capture, Driver Dashboard, app 5.7.1 | Backup phone screenshot; do not use without explaining that the header says telemetry is unavailable while a saved/example dashboard is shown |
| `tablet-01-setup-lab.png` | GitHub Actions Android tablet emulator runtime capture from run 43381049191, app 5.7.1 | Tablet screenshot candidate |

The phone images are centered on a 640 × 1280 dark canvas to meet the Play screenshot aspect-ratio limit without cropping the genuine UI. The tablet image is unaltered. The final listing should use fresh captures from the verified release candidate if its UI differs materially from 5.7.1. Confirm the store icon and graphic are legible in Play's previews before submission.
