from dataclasses import dataclass


@dataclass
class SubtitleStyle:
    font_family: str = "Arial"
    font_size: int = 22
    primary_color: str = "&H00FFFFFF"  # ASS AABBGGRR
    outline_color: str = "&H00000000"
    back_color: str = "&H80000000"
    bold: bool = False
    italic: bool = False
    uppercase: bool = False
    outline: float = 2.0
    shadow: float = 1.0
    alignment: int = 2
    margin_l: int = 10
    margin_r: int = 10
    margin_v: int = 30
    has_box: bool = True
    box_padding_h: int = 0
    wrap_chars: int = 72

    def to_ass_style(self) -> str:
        bold_val = -1 if self.bold else 0
        italic_val = -1 if self.italic else 0
        border_style = 3 if self.has_box else 1
        return (
            f"Style: Default,{self.font_family},{self.font_size},"
            f"{self.primary_color},&H000000FF,{self.outline_color},{self.back_color},"
            f"{bold_val},{italic_val},0,0,100,100,0,0,{border_style},"
            f"{self.outline},{self.shadow},{self.alignment},"
            f"{self.margin_l},{self.margin_r},{self.margin_v},0"
        )


def hex_to_ass(hex_color: str, alpha: str = "00") -> str:
    """Converts #RRGGBB to ASS color (&HAABBGGRR)."""
    color = (hex_color or "#FFFFFF").strip().lstrip("#")
    if len(color) != 6:
        color = "FFFFFF"
    rr = color[0:2]
    gg = color[2:4]
    bb = color[4:6]
    return f"&H{alpha}{bb}{gg}{rr}"


def ass_alpha_from_opacity(opacity_percent: int) -> str:
    """Convert opacity percent (0-100) to ASS alpha hex where 00 is opaque and FF is transparent."""
    clamped = max(0, min(100, int(opacity_percent)))
    alpha = int(round((100 - clamped) * 255 / 100))
    return f"{alpha:02X}"


def position_to_alignment(label: str) -> int:
    mapping = {
        "Bottom center": 2,
        "Top center": 8,
        "Middle center": 5,
    }
    return mapping.get(label, 2)
