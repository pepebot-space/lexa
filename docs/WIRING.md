# Wiring — lexa (Viam Rover 1 + MAX98357A audio)

Hardware wiring for the **lexa** voice/video assistant: a **Viam Rover 1** chassis
with a **MAX98357A** I2S amplifier for speaker output.

- **Board:** Raspberry Pi 3 Model B (40-pin header).
- **Pin numbering:** "Pin" = physical board pin (1–40); "GPIO" = BCM number.
  Viam board configs use **physical pin numbers**.

---

## Pin map

### Viam Rover 1 components

| Component | Component Pin | Phys. Pin | GPIO | Wire |
|---|---|---|---|---|
| Buck Converter | GND | 39 | GND | black |
| Buck Converter | 5V | 4 | 5V | red |
| Accelerometer (ADXL345, I2C) | GND | 34 | GND | black |
| Accelerometer | 3.3V | 17 | 3V3 | red |
| Accelerometer | SDA | 3 | GPIO2 | maroon |
| Accelerometer | SCL | 5 | GPIO3 | pink |
| L298N Motor Driver | En B | 22 | GPIO25 | gray |
| L298N Motor Driver | In 4 | 18 | GPIO24 | yellow |
| L298N Motor Driver | In 3 | 16 | GPIO23 | white |
| L298N Motor Driver | In 2 | 13 | GPIO27 | green |
| L298N Motor Driver | In 1 | 11 | GPIO17 | blue |
| L298N Motor Driver | En A | 15 | GPIO22 | purple |
| L298N Motor Driver | GND | 6 | GND | black |
| L298N Motor Driver | 3.3V | 1 | 3V3 | red |
| L298N Motor Driver | Encoder Left | 29 | GPIO5 | yellow |
| L298N Motor Driver | Encoder Right | 37 | GPIO26 | white |

### MAX98357A I2S amplifier

| MAX98357A Pin | Phys. Pin | GPIO | Notes |
|---|---|---|---|
| `LRC` (LRCLK) | 35 | GPIO19 | I2S word select |
| `BCLK` | 12 | GPIO18 | I2S bit clock |
| `DIN` | 40 | GPIO21 | I2S data |
| `Vin` | 2 | 5V | 5V power |
| `GND` | 9 | GND | ground |
| `GAIN` | — | — | floating = 9 dB; tie to `Vin` = 6 dB (less hiss); 100 kΩ→`Vin` = 3 dB |
| `SD` | — | — | tie to `Vin` for always-on |

**Free pins for expansion:** 31 (GPIO6), 32 (GPIO12), 33 (GPIO13), 36 (GPIO16),
and the SPI0 group 19/21/23/24/26 if SPI is unused.

---

## Software configuration

### 1. Enable the I2S DAC (`/boot/firmware/config.txt`)

```ini
dtoverlay=hifiberry-dac
```

The MAX98357A speaks plain I2S, so the generic `hifiberry-dac` overlay drives it.
Reboot after editing.

### 2. Default audio output (`/etc/asound.conf`)

```
pcm.!default {
    type plug                       # auto-resample 24kHz mono -> 48kHz stereo
    slave.pcm { type hw card "sndrpihifiberry" }
}
ctl.!default { type hw card "sndrpihifiberry" }
```

### 3. Viam encoder config

Left encoder pin = `29`:

```json
{
  "name": "lenc",
  "type": "encoder",
  "model": "incremental",
  "attributes": { "pins": { "i": "29" }, "board": "local" }
}
```

(Pin numbers in Viam are physical board pins. Adjust model/attribute names to match
your existing config.)

---

## Verification

```bash
aplay -l | grep hifiberry                          # -> card 1: sndrpihifiberry
speaker-test -D default -t sine -f 440 -c 2 -l 1   # tone out of the MAX98357A
cd ~/lexa && python3 client-video.py               # full client (mic + webcam + speaker)
```
