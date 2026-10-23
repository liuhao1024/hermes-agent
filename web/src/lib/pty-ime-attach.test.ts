// @vitest-environment jsdom
import { describe, expect, it } from "vitest";

import {
  IME_ATTACHABLE_OPACITY,
  makeXtermTextareaImeAttachable,
} from "./pty-ime-attach";

function stockXtermTextarea(): HTMLTextAreaElement {
  const textarea = document.createElement("textarea");
  textarea.className = "xterm-helper-textarea";
  return textarea;
}

describe("makeXtermTextareaImeAttachable", () => {
  it("lifts the stock opacity:0 / z-index:-5 hiding to IME-attachable values (#143761)", () => {
    const textarea = stockXtermTextarea();

    makeXtermTextareaImeAttachable(textarea);

    expect(textarea.style.opacity).toBe(IME_ATTACHABLE_OPACITY);
    expect(textarea.style.opacity).not.toBe("0");
    expect(textarea.style.zIndex).toBe("0");
  });

  it("keeps the textarea invisible to the eye and to taps", () => {
    const textarea = stockXtermTextarea();

    makeXtermTextareaImeAttachable(textarea);

    // Composition text passing through the textarea must never flash.
    expect(textarea.style.color).toBe("transparent");
    // xterm positions the textarea at the cursor cell; once z-index is
    // lifted above the canvas it must not swallow the terminal's taps.
    expect(textarea.style.pointerEvents).toBe("none");
  });

  it("leaves xterm's own left/top/width/height positioning untouched", () => {
    const textarea = stockXtermTextarea();
    textarea.style.left = "120px";
    textarea.style.top = "88px";
    textarea.style.width = "4.8px";
    textarea.style.height = "10px";

    makeXtermTextareaImeAttachable(textarea);

    expect(textarea.style.left).toBe("120px");
    expect(textarea.style.top).toBe("88px");
    expect(textarea.style.width).toBe("4.8px");
    expect(textarea.style.height).toBe("10px");
  });

  it("does not break focusability — IME attach needs programmatic focus", () => {
    const textarea = stockXtermTextarea();
    document.body.appendChild(textarea);

    makeXtermTextareaImeAttachable(textarea);
    textarea.focus();

    expect(document.activeElement).toBe(textarea);
  });
});
