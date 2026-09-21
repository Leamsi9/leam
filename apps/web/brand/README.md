# Leam icon artwork

The supplied Leam robot artwork was adapted with the built-in image-generation
tool into `leam-icon-source.png`. Browser canvas exports standard PNG package
sizes without additional artistic edits. A padded opaque variant supports Android
maskable icons. The ICO contains the 16 and 32 pixel PNG exports.

Final generation prompt:
“Edit target: the supplied Leam logo artwork. Create one square production app icon
derived directly from this exact artwork. Preserve the friendly glossy purple and
magenta robot, its curved smiling closed eyes, lilac casing, antenna and sprout on
its chest. Keep its character and design faithfully, no new character or alternative
branding. Composition-only adaptation: remove the bottom Leam wordmark (too small
for favicon), center the robot head and upper body as a bold readable mark on a clean
solid very dark plum background (#170d23), with generous 15 percent edge padding so
mobile circular masks do not clip the face. Preserve the smooth polished 3D illustrated
style. No text, no border, no mockup, no extra objects. A single square flat icon asset,
1024 by 1024.”

The returned master is 1254 square; exported files declare their actual dimensions.
Runtime assets live in `../public/`: `leam-icon-192.png`, `leam-icon-512.png`,
`leam-maskable-512.png`, `apple-touch-icon.png`, `favicon-32.png`, and `favicon.ico`.
