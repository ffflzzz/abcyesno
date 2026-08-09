import React, { useState, useRef, useEffect } from "react";
import Icon from "../Icon.jsx";

/**
 * ImageChip — compact filename chip with hover thumbnail tooltip.
 * Used in the composer (as a DOM node; see Composer.jsx) and in chat
 * message bubbles (React) so pasted screenshots don't dominate the UI.
 */
export default function ImageChip({ src, fileName, onClick, className = "" }) {
  const [show, setShow] = useState(false);
  const [loaded, setLoaded] = useState(false);
  const chipRef = useRef(null);
  const tooltipRef = useRef(null);

  // Position the tooltip so it doesn't overflow the viewport.
  useEffect(() => {
    if (!show || !chipRef.current || !tooltipRef.current) return;
    const chip = chipRef.current.getBoundingClientRect();
    const tip = tooltipRef.current;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    let left = chip.left;
    let top = chip.top - 132; // 120px thumb + 12px gap
    if (left + 160 > vw) left = vw - 168;
    if (left < 8) left = 8;
    if (top < 8) top = chip.bottom + 12;
    tip.style.left = `${left}px`;
    tip.style.top = `${top}px`;
  }, [show]);

  const displayName = fileName || "图片";

  return (
    <>
      <span
        ref={chipRef}
        className={`image-chip ${className}`}
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        onFocus={() => setShow(true)}
        onBlur={() => setShow(false)}
        onClick={onClick}
        role="button"
        tabIndex={0}
        title={displayName}
      >
        <Icon name="image" size={14} />
        <span className="image-chip-name">{displayName}</span>
      </span>
      {show && (
        <div ref={tooltipRef} className={`image-chip-tooltip ${loaded ? "loaded" : ""}`}>
          <img
            src={src}
            alt={displayName}
            onLoad={() => setLoaded(true)}
            onError={() => setLoaded(true)}
          />
        </div>
      )}
    </>
  );
}
