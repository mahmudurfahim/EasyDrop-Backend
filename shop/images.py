"""Photo processing for uploads: nothing is cropped or stretched.

A photo keeps its own shape. If it is bigger than 1000 px on its longer side it is shrunk to fit inside 1000 x 1000
(small photos are never enlarged), then saved as a compressed JPEG. The app decides how to fit the picture in its frame.
"""
import io
from PIL import Image, ImageOps

MAX_SIDE = 1000
MAX_UPLOAD = 8 * 1024 * 1024  # 8 MB per file
MAX_PIXELS = 50_000_000


class BadImage(ValueError):
    pass


def normalize(f):
    """f: an uploaded file or any file-like object. Returns JPEG bytes."""
    try:
        im = Image.open(f)
        if im.width * im.height > MAX_PIXELS:
            raise BadImage("The photo is too large (more than 50 megapixels)")
        im = ImageOps.exif_transpose(im)  # phone photos keep their upright orientation
        if im.mode in ("RGBA", "LA", "P"):  # transparent PNGs get a white background instead of black
            im = im.convert("RGBA")
            bg = Image.new("RGB", im.size, (255, 255, 255))
            bg.paste(im, mask=im.split()[-1])
            im = bg
        else:
            im = im.convert("RGB")
        im.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)  # only shrinks, keeps the aspect ratio
        b = io.BytesIO()
        im.save(b, "JPEG", quality=82, optimize=True)
        return b.getvalue()
    except BadImage:
        raise
    except Exception:
        raise BadImage("This file is not a valid photo (use JPG, PNG or WEBP)")
