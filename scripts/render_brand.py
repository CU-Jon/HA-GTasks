"""Render our SVG primitives into local Home Assistant brand icons using Pillow."""

from pathlib import Path
from xml.etree import ElementTree

from PIL import Image, ImageDraw


def main():
    root = Path(__file__).resolve().parents[1]
    svg = ElementTree.parse(root / "assets/icon.svg").getroot()
    output = root / "custom_components/gtasks/brand"
    output.mkdir(exist_ok=True)
    for size, name in ((256, "icon.png"), (512, "icon@2x.png")):
        scale = size * 4 / 256
        image = Image.new("RGBA", (size * 4, size * 4))
        draw = ImageDraw.Draw(image)
        for element in svg:
            tag = element.tag.rsplit("}", 1)[-1]
            attrs = element.attrib

            def number(key, attrs=attrs, scale=scale):
                return float(attrs[key]) * scale

            if tag == "rect":
                x, y, w, h = (number(key) for key in ("x", "y", "width", "height"))
                draw.rounded_rectangle(
                    (x, y, x + w, y + h), radius=number("rx"), fill=attrs["fill"]
                )
            elif tag == "circle":
                x, y, r = (number(key) for key in ("cx", "cy", "r"))
                draw.ellipse((x - r, y - r, x + r, y + r), fill=attrs["fill"])
            else:
                if tag == "line":
                    points = [(number("x1"), number("y1")), (number("x2"), number("y2"))]
                elif tag == "polyline":
                    points = [
                        tuple(float(v) * scale for v in point.split(","))
                        for point in attrs["points"].split()
                    ]
                else:
                    raise ValueError(f"Unsupported SVG primitive: {tag}")
                width = int(number("stroke-width"))
                draw.line(points, fill=attrs["stroke"], width=width, joint="curve")
                for x, y in points:
                    radius = width / 2
                    draw.ellipse(
                        (x - radius, y - radius, x + radius, y + radius), fill=attrs["stroke"]
                    )
        image.resize((size, size), Image.Resampling.LANCZOS).save(output / name, optimize=True)


if __name__ == "__main__":
    main()
