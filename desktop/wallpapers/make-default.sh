#!/bin/sh
# Render a Nizho default wallpaper: a quiet, nearly flat field with the outline of
# the Nizho mark (the ribbon N) drawn three times as hairlines, each nested inside
# the last and cropped off the bottom-right corner. Only the inner outline carries
# the accent colour. No glow, no gradients to speak of, no text:
# the wallpaper is scenery, the UI carries the words. Fine grain prevents
# banding on 8-bit panels.
# Usage: make-default.sh OUT.png [WIDTH HEIGHT [light|dark [green|blue]]]   (ImageMagick 7)
set -eu
OUT=$1
W=${2:-3840}
H=${3:-2160}
LOOK=${4:-dark}
ACCENT=${5:-green}
case "$ACCENT" in
  green) LINE_ACCENT="#4db491" ;;
  blue)  LINE_ACCENT="#4f86ff" ;;
  *) echo "make-default: accent must be green or blue" >&2; exit 2 ;;
esac

if [ "$LOOK" = light ]; then
  TOP="#eceff3"; BOTTOM="#e4e8ed"; LINE="#c9d0d9"; GREEN_A=0.70
else
  TOP="#17191d"; BOTTOM="#121418"; LINE="#2c3038"; GREEN_A=0.60
fi

# The outline of the mark, traced from the logo artwork (560x452 units). One closed path.
NPATH='M139.5,35.7 C91.0,40.1 50.5,72.8 39.5,116.4 C35.7,131.2 35.5,440.6 39.3,439.5 C57.8,434.0 88.7,417.7 108.5,403.0 C144.5,376.2 160.0,351.5 160.0,320.7 C160.0,315.4 160.4,310.2 160.9,309.2 C161.5,308.3 162.9,305.3 164.1,302.7 C170.3,289.6 189.6,280.6 208.0,282.4 C236.4,285.0 260.6,300.4 335.1,363.1 C406.3,422.9 445.0,443.8 488.3,445.7 C502.0,446.3 502.0,446.3 502.0,343.2 C502.0,229.4 501.8,225.9 495.9,207.2 C476.2,145.8 420.5,109.0 343.0,106.3 C333.4,106.0 324.7,105.3 323.7,104.8 C320.7,103.3 320.9,104.0 326.2,113.5 C347.7,152.3 358.0,191.9 370.0,282.0 C374.7,317.2 377.2,332.4 380.0,343.5 C381.1,347.8 381.8,351.5 381.6,351.7 C381.1,352.3 375.2,332.0 362.8,287.0 C340.0,203.7 323.0,161.1 296.4,120.9 C257.5,62.3 198.7,30.3 139.5,35.7 Z'

# Geometry is designed on a 1440x900 canvas: the mark is scaled by 1.1 and its bottom-right corner sits at
# (1400, 880), so it is whole and the contour lines run off the edges. Three neutral lines follow its outline at
# equal distances, like the lines on a map, so they can never cross. Only the outline itself carries the accent.
awk -v W="$W" -v H="$H" -v path="$NPATH" 'BEGIN {
  kh = H / 900; S = 1.1 * kh; tx = 1400 * (W / 1440) - 560 * S; ty = 880 * kh - 452 * S
  printf "push graphic-context fill white stroke none translate %.2f,%.2f scale %.5f,%.5f path \x27%s\x27 pop graphic-context", tx, ty, S, S, path }' > "$OUT.mvg"
awk -v W="$W" -v H="$H" -v sw="$(( H / 900 + 1 ))" -v path="$NPATH" 'BEGIN {
  kh = H / 900; S = 1.1 * kh; tx = 1400 * (W / 1440) - 560 * S; ty = 880 * kh - 452 * S
  printf "push graphic-context fill none translate %.2f,%.2f scale %.5f,%.5f stroke-width %.4f path \x27%s\x27 pop graphic-context", tx, ty, S, S, sw / S, path }' > "$OUT.accent.mvg"

# Distance from every point to the mark. ImageMagick stores about 100 units per pixel.
magick -size "${W}x${H}" xc:black -alpha off -draw "@$OUT.mvg" -colorspace Gray -depth 16 -negate -threshold 50% \
  -morphology Distance Euclidean:1 "$OUT.dist.png"
HW=$(awk -v H="$H" 'BEGIN { printf "%d", (H / 900 + 1) * 50 }')   # half the line width, in distance units
RINGS=""
for d in 70 140 210; do
  c=$(awk -v d="$d" -v H="$H" 'BEGIN { printf "%d", d * (H / 900) * 100 }')
  RINGS="$RINGS \\( \"$OUT.dist.png\" -fx \"max(0,1-abs(u*65535-$c)/($HW*2))\" \\)"
done
eval "magick $RINGS -evaluate-sequence max \"$OUT.rings.png\""

magick -size "${W}x${H}" gradient:"$TOP"-"$BOTTOM" \
  \( -size "${W}x${H}" xc:"$LINE" "$OUT.rings.png" -compose CopyOpacity -composite \) -compose Over -composite \
  \( -size "${W}x${H}" xc:none -fill none -stroke "$LINE_ACCENT" -draw "@$OUT.accent.mvg" \
     -channel A -evaluate multiply "$GREEN_A" +channel \) -compose Over -composite \
  \( -size "${W}x${H}" xc:gray50 -attenuate 0.05 +noise Gaussian -colorspace gray \) \
  -compose Overlay -composite \
  -depth 8 -strip "$OUT"
rm -f "$OUT.mvg" "$OUT.accent.mvg" "$OUT.dist.png" "$OUT.rings.png"
