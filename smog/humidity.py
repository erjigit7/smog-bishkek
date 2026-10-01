"""Humidity correction for SDS011 readings. Standard library only.

Why: the SDS011 counts particles by laser scattering. In humid air particles take up
water and grow, so the sensor sees more/bigger particles and reports too much PM.
Its datasheet only promises correct readings up to 70% relative humidity, and Bishkek
winter nights (exactly the smog nights) are often at 80-95%.

Formula (kappa-Koehler hygroscopic growth, the "Koehler" option on the sensor.community wiki):

    g(h) = 1 + gamma / (1/h - 1)          h = relative humidity as a fraction, 0 < h < 1
    PM_dry = PM_measured / g(h)

It is the same form as the kappa-Koehler correction of Crilley et al. 2018 (AMT 11, 709)
and Di Antonio et al. 2018 (Sensors 18, 2790), with gamma = kappa / 1.65.

gamma for Bishkek = 0.05, measured, not guessed (`py -m smog.compare_reference`):
SDS011 35677 stood 50 m from the US Embassy reference monitor. Relative to dry hours
(RH < 60%), its reading/reference ratio grew x1.13 at RH 60-80%, x1.36-1.38 at 80-90%
and x1.56-1.69 above 90%, nearly the same in winters 2021/22 and 2022/23. gamma 0.05 roughly
reproduces that (x1.07 / x1.23 / x1.6). The value 0.22 recommended on the wiki
(https://github.com/opendata-stuttgart/meta/wiki/EN-Correction-for-humidity, N. Streibl 2017,
fitted in Germany) predicts
x1.3 / x1.9 / x3.3-4.4 and made our numbers worse against the reference in both winters.
Probably Bishkek winter smoke (coal, wood) takes up less water than that aerosol.

Important: this removes only the humidity part. Even in dry air the SDS011 read 0.4-0.6 of
the reference (likely because it misses the smallest combustion particles), and that factor changed
between winters, so a humidity-corrected reading is still NOT the true PM2.5.

Limits:
- g(h) grows without bound as h -> 1 (fog), so h is capped at MAX_RH. Above ~95%
  the reading is mostly water and no formula recovers the dry mass reliably.
- Humidity comes from ERA5 at the city centre, not from each node's own DHT22/BME280.
  The sensor housing is slightly warmer than outside air, so the humidity inside it is
  lower than ERA5 says; the correction is therefore, if anything, too strong.
"""

GAMMA_PM25 = 0.05
MAX_RH = 95.0   # %, above this: treat as 95% (the formula blows up near 100%)


def growth_factor(rh_percent: float, gamma: float = GAMMA_PM25) -> float:
    """How many times humidity inflates the reading at this relative humidity (%)."""
    h = min(max(rh_percent, 0.0), MAX_RH) / 100
    return 1 + gamma * h / (1 - h)  # = 1 + gamma / (1/h - 1), safe at h = 0


def correct_pm(pm: float, rh_percent: float | None, gamma: float = GAMMA_PM25) -> float:
    """Dry-air estimate of a SDS011 PM reading. Without a humidity value, returns pm as is."""
    if rh_percent is None:
        return pm
    return pm / growth_factor(rh_percent, gamma)
