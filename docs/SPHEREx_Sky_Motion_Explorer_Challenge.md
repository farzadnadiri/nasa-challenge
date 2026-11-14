# SPHEREx Sky Motion Explorer Challenge

## Challenge Overview

Since 2025, NASA’s SPHEREx mission has been mapping the entire sky every six months in 102 bands of near-infrared light, returning images of more than a billion objects. Comets, asteroids, stars, brown dwarfs, and even new planets reveal themselves by shifting position between images, but no one person can sift through all that data alone.

**Your challenge:** Create a public-facing web tool to display images of the sky from the SPHEREx mission, making it easy for anyone to quickly see how they change over time.

## Background

Asteroids and comets orbit around the Sun. Nearby stars march across the sky as they orbit around the Galaxy. The Earth orbits the Sun too, causing objects near us to change their apparent positions. Watching for these motions helps us discover new astronomical objects and measure distances to them. For example, the dwarf planet Pluto was discovered when an astronomer compared two images of the sky taken at different times and recognized that one point of light had shifted between images.

More recently, astronomers have mounted massive searches for nearby objects using this process with help from the public. Hundreds of thousands of volunteers have used this technique to discover asteroids, comets, stars, and brown dwarfs—balls of gas too massive to be considered planets yet without enough mass to burn like stars. In fact, many known comets and brown dwarfs were discovered by volunteers in projects like [Backyard Worlds](http://backyardworlds.org/).

Since May 2025, NASA’s new SPHEREx mission has been returning unique data that could yield new discoveries. SPHEREx is mapping the whole sky once every six months in 102 bands of near-infrared light, making it uniquely sensitive to objects that are rich in hydrogen, water, carbon dioxide, carbon monoxide, and many other molecules.

There will be too much data for any one person or even computer code to completely comprehend. There will be images of more than a billion objects—including brown dwarfs, quasars, comets, asteroids, stars, supernovae, and galaxies—which often overlap or vary in brightness. Creative new tools can help make the most of this treasure trove and enable more people to participate in discoveries.

## Objectives

Create a **public-facing web tool** that displays images of the sky from the SPHEREx mission and makes it easy to see changes over time.

### Core User Experience

1. Users enter the coordinates of a patch of sky: **Right Ascension (RA)** and **Declination (Dec)**.
2. The tool retrieves and displays a series of images of that patch of sky.
3. Users can play the series as a short animation, making moving objects easier to spot.

### Required Test Targets

Test the tool on the following objects:

| Target | Description / Coordinates |
| --- | --- |
| Barnard’s Star | Nearby, high-proper-motion star |
| Epsilon Indi B | Brown-dwarf pair orbiting a star |
| WISE 0855 | Fast-moving brown dwarf; **RA:** 08h55m10.831s; **Dec:** −07°14′42.53″ |
| RW Aurigae | Variable star |
| R Draconis | Variable star |

## Potential Considerations

*The following are optional ideas, not mandatory requirements.*

- **Keep data current:** Design a workflow that can incorporate new SPHEREx data releases with minimal effort.
- **Use the 102 infrared bands:** Give users meaningful spectral viewing options. Consider combining data from each of the six detector arrays to improve signal-to-noise. Bonus: generate spectra on the fly.
- **Compare epochs:** Allow users to add or subtract images taken at different times to highlight changes and moving objects.
- **Adjust field of view:** Let users change the sky area displayed. Larger fields help track fast-moving objects but can increase transfer times.
- **Overlay external catalogs:** Support contextual overlays from missions such as **Gaia** or **WISE**.
- **Shareable URLs:** Accept sky coordinates as URL parameters so views can be linked or embedded in other websites.
- **Display metadata:** Show relevant observation metadata, including contamination flags where available.
- **Use astronomical tools:** Investigate [Astropy](https://www.astropy.org/) for astronomy data processing and [Aladin](https://aladin.cds.unistra.fr/) from CDS for sky visualization.

## Expected Deliverable

A publicly accessible web application through which anyone can enter sky coordinates, inspect time-separated SPHEREx imagery, and visually identify sky objects that move or change in brightness.
