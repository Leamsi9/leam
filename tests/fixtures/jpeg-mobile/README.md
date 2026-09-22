# Synthetic JPEG upload fixtures

These 4 × 3 RGB gradients contain no user photos or private metadata. Generated
once with system Pillow 10.2.0; Pillow is not a runtime or test dependency.

```python
from PIL import Image
image = Image.new('RGB', (4, 3))
image.putdata([(x * 50, y * 70, (x + y) * 30)
               for y in range(3) for x in range(4)])
for progressive, name in [(False, 'baseline.jpg'), (True, 'progressive.jpg')]:
    image.save(name, format='JPEG', quality=82,
               progressive=progressive, optimize=False)
```

Caller tests derive trailing NUL padding, in-stream APP1 EXIF, trailing metadata,
a secondary JPEG, missing EOI, truncated segment length and an apparent EOI inside
APP1 payload. All uploaded/downloaded bytes are checked without re-encoding.
