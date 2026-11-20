"""golem's mark and name, as SVG.

The mark is the golem's head: a domed head with sleepy half-moon eyes, and its chin below,
the mouth the gap between them. It's one path, filled even-odd, so the eyes and mouth are see-through
and it takes whatever color it's put in (currentColor). It was rebuilt from Riggy's drawing as
straight edges and true arcs, measured off it. SMALL is the same head with the mouth and eyes
opened up, for 16-32px, where the regular mouth closes up.

The wordmark is "Golem" in Oxanium 700, as outlines (shaped with its kerning, 1% tracking), so
no font is needed for it. In the lockup the mark stands on the word's baseline, a third of a
letter to its left, 0.94 of the type size tall.
"""

from __future__ import annotations

BOX = "118 128 276 312"  # both cuts: x, y, width, height
MARK = (
    "M160.95 245.57L179.76 174.82A63 63 0 0 1 240.64 128L271.36 128A63 63 0 0 1 332.24 174.82L351.05 245.57A5 5 0 0 1 349.7 250.44"
    "L312.59 286.45A4 4 0 0 1 308.85 287.47L263.2 276.18A30 30 0 0 0 248.8 276.18L203.15 287.47A4 4 0 0 1 199.41 286.45L162.3 250.44"
    "A5 5 0 0 1 160.95 245.57ZM228 216A24 24 0 0 1 180 216ZM332 216A24 24 0 0 1 284 216ZM121.73 360.01L138.38 272.25"
    "A16 16 0 0 1 165.24 263.75L194.18 291.84A11.5 11.5 0 0 0 204.95 294.75L250.6 283.46A22.5 22.5 0 0 1 261.4 283.46L307.05 294.75"
    "A11.5 11.5 0 0 0 317.82 291.84L346.76 263.75A16 16 0 0 1 373.62 272.25L390.27 360.01A16 16 0 0 1 384.79 375.28L307.58 439.65"
    "A1.5 1.5 0 0 1 306.62 440L205.38 440A1.5 1.5 0 0 1 204.42 439.65L127.21 375.28A16 16 0 0 1 121.73 360.01Z"
)
SMALL = (
    "M160.95 245.57L179.76 174.82A63 63 0 0 1 240.64 128L271.36 128A63 63 0 0 1 332.24 174.82L351.05 245.57A5 5 0 0 1 349.7 250.44"
    "L312.59 286.45A4 4 0 0 1 308.85 287.47L263.2 276.18A30 30 0 0 0 248.8 276.18L203.15 287.47A4 4 0 0 1 199.41 286.45L162.3 250.44"
    "A5 5 0 0 1 160.95 245.57ZM234 216A30 30 0 0 1 174 216ZM338 216A30 30 0 0 1 278 216ZM121.73 360.01L136.48 282.25"
    "A16 16 0 0 1 163.35 273.75L188.26 297.94A20 20 0 0 0 206.99 303L252.64 291.71A14 14 0 0 1 259.36 291.71L305.01 303"
    "A20 20 0 0 0 323.74 297.94L348.65 273.75A16 16 0 0 1 375.52 282.25L390.27 360.01A16 16 0 0 1 384.79 375.28L307.58 439.65"
    "A1.5 1.5 0 0 1 306.62 440L205.38 440A1.5 1.5 0 0 1 204.42 439.65L127.21 375.28A16 16 0 0 1 121.73 360.01Z"
)
# "Golem" in font units (1000 to the em), the baseline at y=0
WORD = (
    "M72 -168V-522Q72 -690 240 -690H529L596 -638V-560H246Q216 -560 216 -530V-160Q216 -130 246 -130H472V-290H340V-420H616V0H240"
    "Q72 0 72 -168ZM752 -162V-366Q752 -528 914 -528H1054Q1216 -528 1216 -366V-162Q1216 0 1054 0H914Q752 0 752 -162ZM890 -152"
    "Q890 -120 922 -120H1046Q1078 -120 1078 -152V-376Q1078 -408 1046 -408H922Q890 -408 890 -376ZM1358 -192V-740H1496V-178"
    "Q1496 -116 1560 -109V10Q1358 10 1358 -192ZM1674 -162V-366Q1674 -528 1836 -528H1966Q2128 -528 2128 -366V-213H1812V-152"
    "Q1812 -120 1844 -120H2108V-45L2048 0H1836Q1674 0 1674 -162ZM1812 -319H1990V-376Q1990 -408 1958 -408H1844Q1812 -408 1812 -376Z"
    "M2274 0V-528H2876Q3038 -528 3038 -366V0H2900V-376Q2900 -408 2868 -408H2725V0H2587V-408H2412V0Z"
)


def mark(small: bool = False, cls: str = "mark") -> str:
    """The mark alone, inline: decorative, so screen readers skip it."""
    return (
        f"<svg class={cls} viewBox='{BOX}' fill=currentColor aria-hidden=true>"
        f"<path fill-rule=evenodd d='{SMALL if small else MARK}'/></svg>"
    )


# The mark (312 units tall) scaled to 0.94 em of the word's 1000 and stood on its baseline; the
# word starts 0.3 em after the mark. 12 units below the baseline make room for the l's tail.
LOCKUP = (
    "<svg class=lockup viewBox='0 -940 4238 952' fill=currentColor role=img aria-label=Golem>"
    f"<path fill-rule=evenodd transform='matrix(3.01282 0 0 3.01282 -355.51 -1325.64)' d='{MARK}'/>"
    f"<path transform='translate(1131.54 0)' d='{WORD}'/></svg>"
)

# The tab's icon: the small cut in a square, dark on a light browser and light on a dark one.
FAVICON = (
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='100 128 312 312'><style>path{fill:#1c2023}"
    "@media (prefers-color-scheme: dark){path{fill:#e5e8e8}}</style>"
    f"<path fill-rule='evenodd' d='{SMALL}'/></svg>"
)
