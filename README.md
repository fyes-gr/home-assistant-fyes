# FYES for Home Assistant

Bring your whole FYES home into [Home Assistant](https://www.home-assistant.io/): every
light, dimmer, socket, shutter, thermostat, sensor, door, scenario and the alarm appear
in Home Assistant, live, and Home Assistant can control them.

FYES stays in charge. Every command goes through FYES Cloud on the same path as the FYES
app (command → gateway → CAN bus → the device's echo confirms it), so Home Assistant
only ever shows a state the hardware has confirmed. It works for every FYES home today
and needs an internet connection (it talks to FYES Cloud, `api.fyes.eu`).

*Ελληνικά: [παρακάτω](#ελληνικά).*

## Requirements

- Home Assistant 2025.1 or newer.
- A FYES account with access to the home (the same email you use in the FYES app).

## Installation

### HACS (recommended)

1. In HACS, open the menu (⋮) → **Custom repositories**.
2. Add `https://github.com/fyes-gr/home-assistant-fyes`, category **Integration**.
3. Find **FYES** in HACS, install it, and restart Home Assistant.

### Manual

1. Copy the folder `custom_components/fyes` from this repository into your Home
   Assistant configuration folder, so that you have `config/custom_components/fyes/`
   (with `manifest.json` directly inside it).
2. Restart Home Assistant.

## Setup

**Settings → Devices & services → Add integration → FYES**, then choose one of:

- **Sign in with my email** (default): enter your FYES email, then the 6-digit code FYES
  emails you, then pick the home. FYES creates a connection token for that one home and
  Home Assistant stores only that token (never your app session).
- **I have a token**: in the FYES app open **Settings → Integrations → Home Assistant**,
  create a token (it starts with `fyes_` and is shown once) and paste it.

Each FYES home is its own integration entry; add the integration again for another home.
If you run your own FYES server (for example a local hub), open **Advanced** and change
the **Server**; everyone else leaves it at `https://api.fyes.eu`.

If the token is revoked or your access to the home changes, Home Assistant asks you to
sign in again (a repair notification); your devices, names and automations stay.

## What appears in Home Assistant

One Home Assistant device per FYES device, placed in the area named after its FYES room,
all under a **FYES home** device. Names follow Home Assistant's language: Greek names
when Home Assistant runs in Greek, English otherwise.

| FYES | Home Assistant |
|---|---|
| Light on a relay | `light` (on/off) |
| Dimmer | `light` with brightness |
| Socket, appliance, pump, fan, boiler, AC relay | `switch` (sockets as outlets) |
| Gate, garage door | `switch` (on / off, as in the app) |
| Shutter pair, awning | `cover` (open / close / stop; FYES does not know where an idle shutter stopped, so its position shows as unknown) |
| Door lock, intercom release, the Rent-my-home door | `button` "Open" (the keypad's pulse; `relay_state` shows the relay) |
| Thermostat | `climate` (heat / off, setpoint 5–30 °C in 0.5 steps) |
| Temperature, humidity, power, light level | `sensor` with units |
| Door / window contact, motion, smoke, tamper | `binary_sensor` (on = open / detected) |
| Siren output | `siren` (disabled by default) |
| Wall button | `event` "press" (disabled by default, see Options) |
| Scenario | `scene` |
| Security mode | `alarm_control_panel` (disarmed / home / night / away, no code) |

Raw analog inputs and devices hidden in the app are added disabled; enable the ones you
want under the entity's settings.

While Home Assistant is connected, hiding or showing a device in the FYES app carries
over: a device shown again is enabled (Home Assistant adds it about 30 seconds later), and
one you hide is disabled. An entity you disabled yourself in Home Assistant stays disabled.

### Options

**Settings → Devices & services → FYES → Configure**:

- **Show wall-button presses**: adds an event entity for every wall button, so an
  automation can run when someone presses it.
- **Show devices hidden in the app**.

## How it behaves

- **Confirmed states only.** After you switch something, Home Assistant keeps showing the
  old state until the device confirms the change (usually well under a second). If the
  device does not answer, the state does not change.
- **Live, no polling.** FYES pushes every change over a secure websocket. If the
  connection drops, the entities become unavailable, the integration reconnects (waiting
  1 s, 2 s, 4 s … up to 60 s) and reloads the whole home, so nothing that changed in the
  meantime is missed.
- **Gateway offline.** When the home's FYES gateway is offline, its devices and
  scenarios are unavailable and commands are refused with a clear message.
- **Doors are buttons.** A door is an explicit "Open" press, never a lock state that
  could be guessed.
- **Protected devices** (that the app asks about before switching) carry
  `protected: true` in their attributes; Home Assistant users act explicitly.
- **Permissions.** The token acts as you, for one home, with your role there: a member
  controls what a member may control in the app. Activity in the FYES app shows these
  actions as "Home Assistant". If you are removed from the home, your connection stops
  within seconds and its token is deleted.

## Removing

Delete the integration in Home Assistant: its token is revoked in FYES at the same time.
If you add it again later, it starts from FYES's defaults (anything an option turned on
starts off again). The FYES app (**Settings → Integrations → Home Assistant**) lists every connection with
its last use, and can revoke any of them. If you run your own FYES server and it moved,
use **⋮ → Reconfigure** to change the Server instead of removing the integration.

## Troubleshooting

- **Settings → Devices & services → FYES → ⋮ → Download diagnostics** gives a report
  with the connection state and the home's data; the token and your email are removed.
- Debug logging:

  ```yaml
  logger:
    logs:
      custom_components.fyes: debug
  ```

## Development

The tests run Home Assistant against a fake FYES Cloud that implements the integration
contract (REST + websocket):

```bash
uv venv --python 3.14 .venv
uv pip install --python .venv/bin/python pytest-homeassistant-custom-component
.venv/bin/python -m pytest
```

The suite runs on the newest Home Assistant and on the oldest supported one
(2025.1: `pytest-homeassistant-custom-component==0.13.205 pycares==4.5.0` on Python
3.13, installed with `--prerelease=allow`). HA's blocking-call detector is on in every
test. `.github/workflows/validate.yml` adds the official `hassfest` and HACS checks
once this folder is published as its own repository.

The end-to-end check (a real Home Assistant against the real FYES backend on a test
database) lives in the FYES Cloud repository, `qa/home-assistant/`, because it runs the
backend itself.

---

## Ελληνικά

Φέρτε ολόκληρο το σπίτι σας FYES στο Home Assistant: κάθε φως, ντίμερ, πρίζα, ρολό,
θερμοστάτης, αισθητήρας, πόρτα, σενάριο και ο συναγερμός εμφανίζονται ζωντανά στο
Home Assistant, και το Home Assistant μπορεί να τα ελέγχει.

Το FYES παραμένει υπεύθυνο. Κάθε εντολή περνά από το FYES Cloud με την ίδια διαδρομή
όπως η εφαρμογή FYES (εντολή → πύλη → δίαυλος CAN → η συσκευή επιβεβαιώνει), οπότε το
Home Assistant δείχνει μόνο καταστάσεις που έχει επιβεβαιώσει ο εξοπλισμός. Χρειάζεται
σύνδεση στο διαδίκτυο.

### Απαιτήσεις

- Home Assistant 2025.1 ή νεότερο.
- Λογαριασμός FYES με πρόσβαση στο σπίτι (το ίδιο email που χρησιμοποιείτε στην εφαρμογή).

### Εγκατάσταση

- **HACS**: μενού (⋮) → **Custom repositories** → προσθέστε το `https://github.com/fyes-gr/home-assistant-fyes`
  με κατηγορία **Integration** → εγκαταστήστε το **FYES** → επανεκκίνηση.
- **Χειροκίνητα**: αντιγράψτε τον φάκελο `custom_components/fyes` μέσα στον φάκελο
  ρυθμίσεων του Home Assistant, ώστε να υπάρχει `config/custom_components/fyes/`, και
  κάντε επανεκκίνηση.

### Ρύθμιση

**Ρυθμίσεις → Συσκευές και υπηρεσίες → Προσθήκη ενσωμάτωσης → FYES** και επιλέξτε:

- **Σύνδεση με το email μου**: γράψτε το email σας, μετά τον εξαψήφιο κωδικό που σας
  στέλνει το FYES, και επιλέξτε σπίτι. Το Home Assistant κρατά μόνο ένα κλειδί σύνδεσης
  για αυτό το σπίτι, ποτέ τη συνεδρία της εφαρμογής σας.
- **Έχω κλειδί σύνδεσης (token)**: στην εφαρμογή FYES ανοίξτε **Ρυθμίσεις →
  Ενσωματώσεις → Home Assistant**, δημιουργήστε κλειδί (ξεκινά με `fyes_` και φαίνεται
  μία φορά) και επικολλήστε το.

Κάθε σπίτι FYES είναι ξεχωριστή καταχώριση. Αν ανακληθεί το κλειδί ή αλλάξει η πρόσβασή
σας, το Home Assistant σας ζητά να συνδεθείτε ξανά, χωρίς να χαθούν συσκευές, ονόματα ή
αυτοματισμοί.

### Τι θα δείτε

Μία συσκευή Home Assistant για κάθε συσκευή FYES, στον χώρο με το όνομα του δωματίου της.
Τα ονόματα είναι στα ελληνικά όταν το Home Assistant είναι στα ελληνικά.

| FYES | Home Assistant |
|---|---|
| Φως σε ρελέ / ντίμερ | `light` (με φωτεινότητα για τα ντίμερ) |
| Πρίζα, συσκευή, αντλία, ανεμιστήρας, θερμοσίφωνας, κλιματιστικό | `switch` |
| Αυλόπορτα, γκαραζόπορτα | `switch` (άνοιγμα / κλείσιμο, όπως στην εφαρμογή) |
| Ρολά, τέντες | `cover` (άνοιγμα / κλείσιμο / στοπ· η θέση ενός σταματημένου ρολού είναι άγνωστη) |
| Κλειδαριά, θυροτηλέφωνο, πόρτα «Νοικιάστε το σπίτι» | `button` «Άνοιγμα» (`relay_state` δείχνει το ρελέ) |
| Θερμοστάτης | `climate` (θέρμανση / κλειστό, 5–30 °C) |
| Θερμοκρασία, υγρασία, κατανάλωση, φωτεινότητα | `sensor` με μονάδες |
| Επαφή πόρτας / παραθύρου, κίνηση, καπνός, σαμποτάζ | `binary_sensor` |
| Σειρήνα | `siren` (απενεργοποιημένη αρχικά) |
| Διακόπτης τοίχου | `event` «πάτημα» (δείτε τις Επιλογές) |
| Σενάριο | `scene` |
| Λειτουργία ασφαλείας | `alarm_control_panel` (χωρίς κωδικό, όπως στην εφαρμογή) |

**Επιλογές** (Ρυθμίσεις → Συσκευές και υπηρεσίες → FYES → Διαμόρφωση): εμφάνιση
πατημάτων διακοπτών τοίχου, εμφάνιση συσκευών που είναι κρυμμένες στην εφαρμογή.

Όσο το Home Assistant είναι συνδεδεμένο, όταν κρύβετε ή εμφανίζετε μια συσκευή στην
εφαρμογή FYES, το ίδιο γίνεται και εκεί: μια συσκευή που εμφανίζεται ξανά ενεργοποιείται
(το Home Assistant την προσθέτει σε περίπου 30 δευτερόλεπτα) και μια κρυμμένη
απενεργοποιείται. Μια οντότητα που απενεργοποιήσατε εσείς στο Home Assistant μένει
απενεργοποιημένη.

### Πώς λειτουργεί

- **Μόνο επιβεβαιωμένες καταστάσεις**: μετά από μια εντολή, η κατάσταση αλλάζει μόλις
  απαντήσει η συσκευή.
- **Ζωντανά**: το FYES στέλνει κάθε αλλαγή αμέσως. Αν κοπεί η σύνδεση, οι οντότητες
  γίνονται «μη διαθέσιμες», η ενσωμάτωση ξανασυνδέεται και φορτώνει ξανά όλο το σπίτι.
- **Πύλη εκτός σύνδεσης**: οι συσκευές γίνονται μη διαθέσιμες και οι εντολές
  απορρίπτονται με σαφές μήνυμα.
- **Οι πόρτες είναι κουμπιά**: πάντα ρητό «Άνοιγμα», ποτέ κατάσταση κλειδαριάς.
- Το κλειδί ενεργεί ως εσείς, για ένα σπίτι, με τον ρόλο σας εκεί. Στη Δραστηριότητα της
  εφαρμογής οι ενέργειες φαίνονται ως «Home Assistant».

### Αφαίρεση

Διαγράψτε την ενσωμάτωση στο Home Assistant: το κλειδί της ανακαλείται ταυτόχρονα στο FYES.
Αν την προσθέσετε ξανά αργότερα, ξεκινά από τις προεπιλογές του FYES (ό,τι είχε ανοίξει μια
επιλογή ξεκινά ξανά κλειστό). Αν σας αφαιρέσουν από το σπίτι, η σύνδεσή σας σταματά μέσα σε
λίγα δευτερόλεπτα και το κλειδί της διαγράφεται.
Η εφαρμογή FYES (**Ρυθμίσεις → Ενσωματώσεις → Home Assistant**) δείχνει κάθε σύνδεση με την
τελευταία της χρήση και μπορεί να ανακαλέσει οποιαδήποτε.
