from __future__ import annotations

# Wplace palette names and RGB values. Names intentionally remain in the
# terminology used by Wplace rather than being translated per report locale.
WPLACE_COLOR_NAMES: dict[str, str] = {
    "#000000": "Black",
    "#3C3C3C": "Dark Gray",
    "#787878": "Gray",
    "#AAAAAA": "Medium Gray",
    "#D2D2D2": "Light Gray",
    "#FFFFFF": "White",
    "#600018": "Deep Red",
    "#A50E1E": "Dark Red",
    "#ED1C24": "Red",
    "#FA8072": "Light Red",
    "#E45C1A": "Dark Orange",
    "#FF7F27": "Orange",
    "#F6AA09": "Gold",
    "#F9DD3B": "Yellow",
    "#FFFABC": "Light Yellow",
    "#9C8431": "Dark Goldenrod",
    "#C5AD31": "Goldenrod",
    "#E8D45F": "Light Goldenrod",
    "#4A6B3A": "Dark Olive",
    "#5A944A": "Olive",
    "#84C573": "Light Olive",
    "#0EB968": "Dark Green",
    "#13E67B": "Green",
    "#87FF5E": "Light Green",
    "#0C816E": "Dark Teal",
    "#10AEA6": "Teal",
    "#13E1BE": "Light Teal",
    "#0F799F": "Dark Cyan",
    "#60F7F2": "Cyan",
    "#BBFAF2": "Light Cyan",
    "#28509E": "Dark Blue",
    "#4093E4": "Blue",
    "#7DC7FF": "Light Blue",
    "#4D31B8": "Dark Indigo",
    "#6B50F6": "Indigo",
    "#99B1FB": "Light Indigo",
    "#4A4284": "Dark Slate Blue",
    "#7A71C4": "Slate Blue",
    "#B5AEF1": "Light Slate Blue",
    "#780C99": "Dark Purple",
    "#AA38B9": "Purple",
    "#E09FF9": "Light Purple",
    "#CB007A": "Dark Pink",
    "#EC1F80": "Pink",
    "#F38DA9": "Light Pink",
    "#9B5249": "Dark Peach",
    "#D18078": "Peach",
    "#FAB6A4": "Light Peach",
    "#684634": "Dark Brown",
    "#95682A": "Brown",
    "#DBA463": "Light Brown",
    "#7B6352": "Dark Tan",
    "#9C846B": "Tan",
    "#D6B594": "Light Tan",
    "#D18051": "Dark Beige",
    "#F8B277": "Beige",
    "#FFC5A5": "Light Beige",
    "#6D643F": "Dark Stone",
    "#948C6B": "Stone",
    "#CDC59E": "Light Stone",
    "#333941": "Dark Slate",
    "#6D758D": "Slate",
    "#B3B9D1": "Light Slate",
}


def normalize_hex(value: str) -> str:
    text = str(value or "").strip().upper()
    if len(text) == 6:
        text = "#" + text
    return text


def wplace_color_name(value: str) -> str | None:
    return WPLACE_COLOR_NAMES.get(normalize_hex(value))


def wplace_color_label(value: str, *, include_hex: bool = True) -> str:
    code = normalize_hex(value)
    name = wplace_color_name(code)
    if not name:
        return code or str(value)
    return f"{name} ({code})" if include_hex else name
