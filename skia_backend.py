"""CPU Skia raster backend; the shared CLI owns scheduling and safe saves.

Pillow supplies font selection/anchor metrics and validates completed PNGs.
Skia draws, optionally downsamples, and encodes PNGs. No GPU is required.
"""
import math

import skia


class SkiaRenderer:
    def __init__(self, width, height, aa, metrics_font, style):
        self.width, self.height, self.aa = width, height, aa
        self.metrics_font = metrics_font
        self.style = style
        self.scale = min(width / style.WIDTH, height / style.HEIGHT)
        self.cx = width * .5 * aa
        self.cy = height * .52 * aa
        self.radius = min(width * .34, height * .28) * aa
        self.surface = skia.Surface(width * aa, height * aa)
        self.output = skia.Surface(width, height) if aa > 1 else None
        if self.surface is None or (aa > 1 and self.output is None):
            raise RuntimeError("Could not allocate Skia rendering surface")
        typeface = skia.Typeface.MakeFromFile(str(metrics_font.path))
        if typeface is None:
            raise RuntimeError(f"Skia could not load font: {metrics_font.path}")
        self.font = skia.Font(typeface, metrics_font.size)
        self.font.setEdging(skia.Font.Edging.kAntiAlias)
        self.font.setSubpixel(True)
        self.arc_paint = self.paint(style.ARC_COLOR)
        self.arc_paint.setStyle(skia.Paint.kStroke_Style)
        self.arc_width = max(1, round(style.ARC_WIDTH * aa * self.scale))
        self.arc_paint.setStrokeWidth(self.arc_width)
        # Pillow strokes its ellipse inside the bounding rectangle. Skia
        # centers strokes on the path, so inset by half the stroke width.
        r = self.radius - self.arc_width / 2
        self.arc_rect = skia.Rect.MakeLTRB(self.cx-r, self.cy-r, self.cx+r, self.cy+r)
        self.dot_paint = self.paint(style.DOT_COLOR)
        self.tick_paint = self.paint(style.DOT_COLOR)
        self.tick_paint.setStrokeWidth(max(1, round(style.ZERO_TICK_WIDTH * aa * self.scale)))
        self.text_paint = self.paint(style.TEXT_COLOR)
        self.label_offsets = {}

    @staticmethod
    def paint(rgba):
        return skia.Paint(Color=skia.ColorSetARGB(rgba[3], *rgba[:3]), AntiAlias=True)

    def save(self, output_file, angle):
        canvas = self.surface.getCanvas()
        canvas.clear(skia.ColorTRANSPARENT)
        canvas.drawArc(self.arc_rect, 180, 180, False, self.arc_paint)
        theta = math.radians(270 + angle)
        canvas.drawCircle(self.cx + self.radius * math.cos(theta),
                          self.cy + self.radius * math.sin(theta),
                          self.style.DOT_RADIUS * self.aa * self.scale, self.dot_paint)
        apex = self.cy - self.radius
        tick = self.style.ZERO_TICK_LENGTH * self.aa * self.scale
        canvas.drawLine(self.cx, apex - tick * .8, self.cx, apex + tick * .2, self.tick_paint)
        label = f"{abs(round(angle))}°"
        if label not in self.label_offsets:
            # Translate Pillow's middle/middle anchor to Skia's left/baseline.
            middle = self.metrics_font.getbbox(label, anchor="mm")
            baseline = self.metrics_font.getbbox(label, anchor="ls")
            self.label_offsets[label] = (middle[0]-baseline[0], middle[1]-baseline[1])
        dx, dy = self.label_offsets[label]
        canvas.drawString(label, self.cx + dx, self.height * .73 * self.aa + dy,
                          self.font, self.text_paint)
        image = self.surface.makeImageSnapshot()
        if self.output is not None:
            out = self.output.getCanvas()
            out.clear(skia.ColorTRANSPARENT)
            out.drawImageRect(image, skia.Rect.MakeWH(self.width, self.height),
                              skia.SamplingOptions(skia.CubicResampler.Mitchell()))
            image = self.output.makeImageSnapshot()
        data = image.encodeToData(skia.kPNG, 100)
        if data is None:
            raise RuntimeError("Skia PNG encoding failed")
        with open(output_file, "wb") as destination:
            destination.write(bytes(data))
