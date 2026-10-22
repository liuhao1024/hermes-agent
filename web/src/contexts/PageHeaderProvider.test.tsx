// @vitest-environment jsdom
// #142836: the mobile nav locks scrolling via `document.body.style.overflow`,
// but the page actually scrolls inside this provider's <main>. Only the
// `scrollLocked` prop freezes it; assert the class flips so iOS Safari can no
// longer scroll the page behind the open menu.

import { afterEach, describe, expect, it } from "vitest";
import { act } from "react";
import { createRoot, type Root } from "react-dom/client";
import { MemoryRouter } from "react-router";

import { PageHeaderProvider } from "./PageHeaderProvider";

(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT =
  true;

let container: HTMLDivElement;
let root: Root;

function mount(scrollLocked?: boolean) {
  container = document.createElement("div");
  document.body.append(container);
  root = createRoot(container);
  act(() => {
    root.render(
      <MemoryRouter initialEntries={["/sessions"]}>
        <PageHeaderProvider pluginTabs={[]} scrollLocked={scrollLocked}>
          <p>content</p>
        </PageHeaderProvider>
      </MemoryRouter>,
    );
  });
}

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function mainClasses() {
  return container.querySelector("main")!.className;
}

describe("PageHeaderProvider scrollLocked", () => {
  it("keeps <main> scrollable by default", () => {
    mount();
    expect(mainClasses()).toContain("overflow-y-auto");
    expect(mainClasses()).not.toContain("overflow-hidden");
  });

  it("freezes <main> scrolling while the mobile nav is open", () => {
    mount(true);
    expect(mainClasses()).toContain("overflow-hidden");
    expect(mainClasses()).not.toContain("overflow-y-auto");
  });
});
