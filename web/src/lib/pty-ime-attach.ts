// Chromium on Android (Gboard et al.) refuses to raise the soft keyboard for
// xterm's helper textarea: stock xterm.js CSS parks it at `opacity: 0;
// z-index: -5`, and Chromium's IME heuristics treat a fully transparent,
// negatively z-ordered element as non-editable decoration. Firefox for
// Android and desktop browsers attach the IME regardless (#143761), so only
// the touch surface needs this lift: keep the textarea visually invisible
// while making it IME-attachable, and leave it transparent to taps so it
// never intercepts the terminal's own touch handling.
export const IME_ATTACHABLE_OPACITY = "0.01";

export function makeXtermTextareaImeAttachable(
  textarea: HTMLTextAreaElement,
): void {
  textarea.style.opacity = IME_ATTACHABLE_OPACITY;
  textarea.style.zIndex = "0";
  textarea.style.color = "transparent";
  textarea.style.pointerEvents = "none";
}
