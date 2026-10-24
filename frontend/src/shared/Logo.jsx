/* Single source of truth for every brand-asset render in the app. Wraps the real
   logo artwork (frontend/public/brand/, generated from the originals in
   public/brand/source/) — nothing else in the app should reference a raw <img>
   logo path or redraw the mark as an icon/text imitation.

   variant="lockup" (default) — icon + wordmark, for top nav / auth pages / brand rows
   variant="icon"             — icon only, for collapsed nav / mobile / avatars / loaders
   variant="mono"             — flat single-colour icon silhouette, for dark/coloured
                                 surfaces (no current screen needs this yet; prepared
                                 for when one does — same "installed, unused" pattern
                                 as the Spectrum motion tokens). */
import React from "react";

const ICON_SRC = "/brand/icon-256.png";
const ICON_MONO_SRC = "/brand/icon-mono-white.png";
const LOCKUP_SRC = "/brand/lockup-960.png";
const LOCKUP_RATIO = 960 / 151; // generated asset's own width:height

export default function Logo({ variant = "lockup", size = 24, className, alt = "AEOMirror", ...rest }) {
  if (variant === "icon" || variant === "mono") {
    return (
      <img
        src={variant === "mono" ? ICON_MONO_SRC : ICON_SRC}
        alt={alt}
        width={size}
        height={size}
        className={className}
        {...rest}
      />
    );
  }
  const height = size;
  const width = Math.round(height * LOCKUP_RATIO);
  return (
    <img
      src={LOCKUP_SRC}
      alt={alt}
      width={width}
      height={height}
      className={className}
      {...rest}
    />
  );
}
