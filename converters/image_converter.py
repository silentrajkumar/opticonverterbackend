"""Image format conversions."""

from PIL import Image, ImageOps

from .common import ConversionFailure

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass


IMAGE_INPUTS = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".tif", ".tiff", ".heic", ".heif"}
IMAGE_OUTPUTS = {".png", ".jpg", ".jpeg", ".webp"}


def image_convert(source, output, target, quality):
    try:
        with Image.open(source) as opened:
            image = ImageOps.exif_transpose(opened)
            image.seek(0)
            if target == ".pdf":
                image.convert("RGB").save(output, "PDF", resolution=150)
                return

            image_format = {".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG", ".webp": "WEBP"}[target]
            if image_format == "JPEG":
                if image.mode in {"RGBA", "LA"}:
                    rgba = image.convert("RGBA")
                    background = Image.new("RGB", rgba.size, "white")
                    background.paste(rgba, mask=rgba.getchannel("A"))
                    image = background
                else:
                    image = image.convert("RGB")
            elif image_format == "PNG" and image.mode not in {"RGB", "RGBA", "L", "LA"}:
                image = image.convert("RGBA")
            elif image_format == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            options = {"quality": quality} if image_format in {"JPEG", "WEBP"} else {}
            image.save(output, image_format, **options)
    except (OSError, ValueError, KeyError) as exc:
        raise ConversionFailure("The image format could not be decoded. HEIC files require pillow-heif support.") from exc
