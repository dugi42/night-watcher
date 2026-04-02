# Night Watcher — Build & Assembly

Step-by-step photo documentation of the hardware build.

> **Note to maintainer:** replace every `![placeholder](images/build/...)` line
> with an actual photo once it is taken.  Recommended: shoot in good natural
> light, frame the component centrally, include a ruler or coin for scale where
> the size matters.  Store originals in `docs/images/build/` at full resolution;
> compress for the web to ≤ 500 KB per image.

---

## Bill of Materials

| Component | Qty | Notes |
| --- | --- | --- |
| Raspberry Pi 5 — 16 GB | 1 | Main compute board |
| GeeekPi aluminium case + NVMe HAT + PoE | 1 | Passive cooling, M.2 slot, PoE input |
| NVMe SSD — 256 GB (M.2 2230 / 2242) | 1 | Samsung PM991a or WD SN740 recommended |
| Official Raspberry Pi 27 W USB-C PSU | 1 | 5 V / 5 A; critical for YOLO + NVMe load |
| USB night-vision webcam | 1 | ELP or equivalent; IR illuminator built in |
| MicroSD card — 32 GB (boot only) | 1 | OS boot; NVMe carries the data |
| USB-A to USB-A cable or USB extension | 1 | Camera mounting flexibility |
| Weatherproof outdoor enclosure | 1 | IP65 or better for garden deployment |
| Silica gel sachets | 2–3 | Moisture control inside the enclosure |
| M3 standoffs + screws | assorted | Mounting the Pi inside the enclosure |
| PoE injector / PoE switch port | 1 | Powers the Pi over Ethernet (optional) |

---

## Step 1 — Unboxing & inventory check

Lay out every component before assembly.  Confirm you have all parts and check
for shipping damage.

![Unboxing — all components laid out](images/build/01_unboxing.jpg)

*What to capture:* all items spread on a clean surface, labels visible.

---

## Step 2 — NVMe SSD installation

Insert the M.2 SSD into the HAT slot at a 30–45° angle, press flat, and secure
with the retention screw.

![NVMe SSD inserted into the M.2 HAT slot](images/build/02_nvme_install.jpg)

*What to capture:* SSD seated in slot, retention screw visible.

> **Tip:** the Pi 5 only supports PCIe Gen 2 by default; Gen 3 requires adding
> `dtparam=pciex1_gen=3` to `/boot/firmware/config.txt` and may reduce SSD
> compatibility.

---

## Step 3 — Raspberry Pi 5 into the case

Place the Pi 5 onto the case base plate, align the mounting holes, and secure
with the four M2.5 standoffs.  Connect the FPC ribbon between the Pi's PCIe
connector and the HAT before closing the case.

![Pi 5 mounted in case base, FPC ribbon connected](images/build/03_pi_in_case.jpg)

*What to capture:* Pi board seated, ribbon flat and connected at both ends.

---

## Step 4 — Case assembly

Fit the aluminium top cover and tighten the four corner screws finger-tight.
The aluminium shell acts as the heatsink — make sure the thermal pad on the
CPU is making contact with the lid.

![Assembled case, top cover fitted](images/build/04_case_closed.jpg)

*What to capture:* closed case, all screws present, no gaps at the seam.

---

## Step 5 — Camera mounting

Attach the webcam to the enclosure using the camera's integrated mount or a
short arm bracket.  Route the USB cable through a weatherproof cable gland.
Point the camera toward the area you want to monitor.

![Camera mounted on enclosure, aimed at garden](images/build/05_camera_mount.jpg)

*What to capture:* camera position and viewing angle, cable entry point.

---

## Step 6 — Enclosure wiring

Route the Ethernet (PoE) or USB-C power cable through the cable glands.  Tighten
the glands hand-firm.  Place two silica gel sachets inside the enclosure to
absorb moisture before sealing.

![Cables routed through glands, silica gel placed](images/build/06_enclosure_wiring.jpg)

*What to capture:* both cable glands, enclosure interior with cables and sachets.

---

## Step 7 — Final outdoor placement

Mount the sealed enclosure in its final position — on a fence post, wall
bracket, or under an eave.  Ensure the IR illuminator has a clear field of
view and is not pointing into reflective surfaces.

![Enclosure mounted outdoors, final position](images/build/07_final_placement.jpg)

*What to capture:* enclosure in situ, field of view visible in background.

---

## Step 8 — First boot & stack validation

Power on the Pi and wait ~60 s for Docker Compose to start all services.
Open a browser to the Streamlit dashboard and verify the live stream appears.

```bash
# Check all services are up
docker compose ps

# Confirm metrics are being scraped
curl http://<pi-hostname>:9100/metrics | grep night_watcher_hw_cpu
```

![Streamlit dashboard live stream on first boot](images/build/08_first_boot_dashboard.jpg)

*What to capture:* dashboard in browser showing live MJPEG stream and detection
status.

---

## Thermal performance

After running YOLO inference for 30 minutes, check the CPU temperature.  The
aluminium case acts as a passive heatsink; typical steady-state temperature on
Pi 5 at full load is 65–70 °C.

```bash
# From the Pi
vcgencmd measure_temp

# Or from the metrics exporter
curl -s http://<pi-hostname>:9100/metrics | grep temperature
```

![Thermal image or temperature readout after 30 min](images/build/09_thermal.jpg)

*What to capture:* temperature readout or IR thermal image of the case top.

---

## Cable management

![Finished installation with tidy cabling](images/build/10_cable_management.jpg)

*What to capture:* complete outdoor setup with cables zip-tied and protected.
