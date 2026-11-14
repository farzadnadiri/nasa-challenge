# Gaia Data Release 3 (Gaia DR3)

![Gaia mission logo](https://esdcdoi.esac.esa.int/esdc-dois/assets/images/mission_logos/Gaia_logo.png)

**European Space Agency (ESA) — Gaia Mission**

| Field | Value |
| --- | --- |
| Dataset ID | Gaia Data Release 3 (Gaia DR3) |
| DOI | [10.5270/esa-qa4lep3](https://doi.org/10.5270/esa-qa4lep3) |
| Mission | GAIA |
| Category | Astronomy |
| Status | Active |
| Download | [Gaia DR3 data](http://cdn.gea.esac.esa.int/Gaia/gdr3/) |
| Portal | [Gaia Data Release 3](https://www.cosmos.esa.int/web/gaia/data-release-3) |

## Overview

### Contents of Gaia DR3

The third Gaia data release (Gaia DR3) consists of the set of data released as Gaia Early Data Release 3 on 3 December 2020 complemented with new data released on 13 June 2022. The set of data released as Gaia Early Data Release 3 (Gaia EDR3) on 3 December 2020 comprises:

- The full astrometric solution — positions on the sky (α, δ), parallaxes, and proper motions — for around 1.46 billion (1.46 10^9) sources, with a limiting magnitude of about G ≈ 21 and a bright limit of about G ≈ 3. The astrometric solution is accompanied with some new quality indicators, like RUWE, and source image descriptors.
- The full astrometric solution has been done as 5-parameter solution for 585 million sources and as 6-parameter solution for 882 million sources. In the 6-parameter solution, the additional fitted quantity is the so-called pseudo-colour that had to be included for sources without high-quality colour information.
- In addition, two-parameters solutions - positions on the sky (α, δ) - for around 344 million additional sources.
- G magnitudes for around 1.806 billion sources (with the [known issue](https://www.cosmos.esa.int/web/gaia/edr3-known-issues#GBandCorrections) present in EDR3 corrected in Gaia DR3).
- G_BP and G_RP magnitudes for around 1.54 billion and 1.55 billion sources, respectively.

In Gaia Data Release 3 (Gaia DR3), the above set of data is complemented with new products released on 13 June 2022:

- Object classifications for 1.59 billion sources and astrophysical parameters (T_eff, logg, [M/H], A_G, distance, etc.) from BP/RP spectra for 470 million objects, including MCMC samples for most sources with astrophysical parameters. Other astrophysical parameters from the BP/RP spectra include:
  - Spectral types (217 million stars) and emission-line star classifications (57,000 stars);
  - Spectroscopic parameters for 2.3 million hot stars, 94,000 ultra-cool stars, activity index for 1.3 million cool stars, and H-alpha emission for 235 million stars;
  - Evolutionary parameters (mass and age) for 128 million stars;
  - Astrophysical parameters for 348 million objects based on the assumption of an unresolved binary in the BP/RP spectra;
  - Self-organised map (outlier) analysis based on 56 million sources with the weakest object classifications.
- Astrophysical parameters (T_eff, logg, [M/H], [X/M] for 12 elements, etc.) from RVS spectra for 5.5 million objects, including diffuse interstellar bands for 472,000 objects.
- All-sky total galactic extinction maps at 4 different spatial resolutions (HEALPix levels 6, 7, 8, and 9).
- Mean BP/RP spectra for 219 million sources, most of them with G < 17.6 mag.
- Mean RVS spectra for 1 million well-behaved objects.
- Mean radial velocities for 33 million stars and mean G_RVS magnitudes for 32 million objects with G_RVS <\~ 14 mag with effective temperatures (T_eff) in the range of \~3100 to 14,500 K.
- Rotational velocities for 3.5 million sources with G_RVS <\~ 12 mag.
- Variability analysis, together with the underlying epoch photometry, for 10.5 million sources. Apart from classification into 24 variability classes, detailed variability results are provided in separate tables for the following candidates:
  - Cepheids (15,021 objects);
  - Compact companions (6306 objects);
  - Eclipsing binaries (2,184,477 objects);
  - Long-period variables (1,720,588 objects);
  - Microlensing events (363 objects);
  - Planetary transits (214 objects; see the note below);
  - RR Lyrae stars (271,779 objects);
  - Short-timescale variables (471,679 objects);
  - Solar-like rotational modulation variables (474,026 objects);
  - Upper-main-sequence oscillators (54,476 objects);
  - Active galactic nuclei (872,228 objects).
- Solar-system results for 158,000 sources (including 31 planetary satellites), with orbital solutions and individual epoch observations for 154,000 objects and with mean BP/RP reflectance spectra for more than 60,000 objects.
- Some 813,000 non-single stars, including amongst others non-single-star models for sources compatible with an astrometric acceleration solution, non-single-star orbital models for spectroscopic binaries compatible with a trend, and non-single-star orbital models for sources compatible with a two-body solution.
- Some 6.6 million quasar candidates with redshift estimates for most of them.
- Some 1.1 million quasars analysed with 60,000 host galaxies detected and 15,000 surface brightness profiles of the host galaxy.
- Some 4.8 million galaxy candidates with redshift estimates for more than 1 million objects.
- Some 900,000 galaxies analysed with two surface brightness profiles.
- The Gaia Andromeda Photometric Survey (GAPS), consisting of the photometric time series for all 1.2 million sources located in a 5.5-degree radius field centred on the Andromeda galaxy.
- Selected tables from Gaia Collaboration performance verification papers published with Gaia DR3.
- All 2612 science alerts triggered in the period underlying Gaia DR3.

The new data set neither contains new astrometry nor new photometric calibrations such that the following elements are in common for and apply to both Gaia EDR3 and Gaia DR3:

- About 1.61 million celestial reference frame (Gaia-CRF3) sources.
- Cross-matches between Gaia (E)DR3 sources on the one hand and Hipparcos-2, Tycho-2 + TDSC merged, 2MASS PSC (merged with 2MASS XSC), SDSS DR13, Pan-STARRS1 DR1, SkyMapper DR2, GSC 2.3, APASS DR9, RAVE DR5, allWISE, URAT-1, and RAVE DR6 data on the other hand.
- Additionally, a Gaia DR2 to Gaia (E)DR3 match table.
- Full photometric passband definitions for G, G_BP, G_RP, and G_RVS. More information is available [here](https://www.cosmos.esa.int/web/gaia/edr3-passbands). Please be aware that the photometric system for the G, G_BP, and G_RP bands in Gaia (E)DR3 is different from the photometric systems as used in Gaia DR2 and in Gaia DR1.
- Simulated data from the Gaia Object Generator (GOG) and the Gaia Universe Model Snapshot (GUMS).
- The commanded scan law covering the Gaia (E)DR3 data collection period. Also the major periods where data was not sent to the ground or could not be processed are identified.

All of the above combined makes up the full Gaia Data Release 3.

For more information, see the [Gaia DR3 portal](https://www.cosmos.esa.int/web/gaia/data-release-3), [Gaia DR3 papers](https://www.cosmos.esa.int/web/gaia/dr3-papers), and [Gaia DR3 documentation](https://gea.esac.esa.int/archive/documentation/GDR3/index.html).

### Mission Description

Gaia is a European space mission providing astrometry, photometry, and spectroscopy of more than 1000 million stars in the Milky Way. Also data for significant samples of extragalactic and Solar system objects is made available. The Gaia Archive contains deduced positions, parallaxes, proper motions, radial velocities, and brightnesses. Complementary information on multiplicity, photometric variability, and astrophysical parameters is provided for a large fraction of sources.

Gaia Collaboration et al. (2016): "The Gaia mission", Astronomy & Astrophysics, 595, A1; [https://doi.org/10.1051/0004-6361/201629272](https://doi.org/10.1051/0004-6361/201629272)

## Metadata

| Field | Value |
| --- | --- |
| Mission | GAIA |
| Version | 1.1 |
| Product Type | Dataset |
| Record Status | Active |
| Landing Page Publication Date | June 13, 2022 |
| Temporal Coverage | July 25, 2014 (10:30 UTC) to May 28, 2017 (08:44 UTC) |
| Keywords | GAIA, GAIA DR3, GAIA Data Release 3, sources, Cepheids, Astrometric Solution, RR Lyrae, Tycho-Gaia, asteroids, Gaia-CRF, Hipparcos-2, Tycho-2, 2MASS, PSC, SDSS, DR9, Pan-STARRS1, GSC2.3, PPM-XL, ALLWISE, URAT-1, Radial Velocities, Astronomical Parameters, Variable Stars, Solar System Objects, passbands |
| Publisher | European Space Agency |

## Credits & Contacts

| Field | Value |
| --- | --- |
| Creator | Gaia Collaboration 2022 |
| Contributors | Gaia Data Processing and Analysis Consortium (DPAC) |
| Creator Contact | Gaia Helpdesk ([gaia-helpdesk@esa.int](mailto:gaia-helpdesk@esa.int) or [support ticketing system](https://support.cosmos.esa.int/gaia/)) |
| Department | D/SCI |

### Credit Guidelines

Gaia Collaboration 2022, 2022, 'Gaia Data Release 3 (Gaia DR3)', 1.1, European Space Agency, [https://doi.org/10.5270/esa-qa4lep3](https://doi.org/10.5270/esa-qa4lep3)

## Links

| Resource | Link / Details |
| --- | --- |
| Portal | [Gaia Data Release 3](https://www.cosmos.esa.int/web/gaia/data-release-3) |
| Download | [Gaia DR3 data](http://cdn.gea.esac.esa.int/Gaia/gdr3/) |
| Publication | Gaia Collaboration et al. (2023), "Gaia Data Release 3. Summary of the contents and survey properties", *Astronomy & Astrophysics*, 674, A1; [DOI: 10.1051/0004-6361/202243940](https://doi.org/10.1051/0004-6361/202243940) |
| Rights | Data hosted in the ESA Space Science Archives are distributed under the [CC-BY-NC-3.0-IGO](https://creativecommons.org/licenses/by-nc/3.0/igo) license. |
