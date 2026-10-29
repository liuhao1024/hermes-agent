import { afterEach, describe, expect, it, vi } from "vitest";

import {
  dashboardServingProfile,
  initialProfileScope,
  shouldAdoptActiveProfile,
} from "./profile-bootstrap";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("initialProfileScope", () => {

  it("does not replace a launch profile with the sticky active profile", () => {
    expect(
      shouldAdoptActiveProfile(null, "worker_x", "default", "review"),
    ).toBe(false);
  });

  it("uses the sticky active profile without a URL or launch profile", () => {
    expect(
      shouldAdoptActiveProfile(null, "", "default", "review"),
    ).toBe(true);
  });

  it("keeps a named-profile dashboard on its own profile (#96712)", () => {
    // `hermes -p demo serve` with the machine's sticky active profile on
    // `default`: the dashboard must not retarget chats to the other agent.
    expect(shouldAdoptActiveProfile(null, "", "demo", "default")).toBe(false);
    expect(shouldAdoptActiveProfile(null, "", "custom", "default")).toBe(false);
  });
});

describe("dashboardServingProfile", () => {
  it("names no profile when there is no window at all", () => {
    expect(dashboardServingProfile()).toBe("");
  });

  it.each([
    ["an injected serving profile", { __HERMES_DASHBOARD_PROFILE__: "served" }, "served"],
    ["a window without one", {}, ""],
  ])("reports %s", (_label, windowStub, expected) => {
    vi.stubGlobal("window", windowStub);
    expect(dashboardServingProfile()).toBe(expected);
  });
});

describe("initialProfileScope precedence", () => {
  // URL > bootstrap > serving. The serving profile is the LAST resort: it says
  // out loud what an unnamed request already meant, so it must never override a
  // scope the URL or the bootstrap payload already named.
  it.each([
    ["the URL profile outranks bootstrap and serving", "profile=url", "boot", "served", "url"],
    ["an explicit empty URL profile still outranks both", "profile=", "boot", "served", ""],
    ["the bootstrap profile outranks the serving profile", "resume=s1", "boot", "served", "boot"],
    ["the serving profile is used when nothing else names one", "resume=s1", "", "served", "served"],
    ["no scope is invented when nothing names one", "resume=s1", "", "", ""],
  ])("%s", (_label, query, bootstrap, serving, expected) => {
    expect(
      initialProfileScope(new URLSearchParams(query), bootstrap, serving),
    ).toBe(expected);
  });

  it("defaults the serving profile to the one this backend injected", () => {
    vi.stubGlobal("window", { __HERMES_DASHBOARD_PROFILE__: "served" });
    expect(initialProfileScope(new URLSearchParams("resume=s1"), "")).toBe("served");
  });
});

describe("dashboardInitialProfile consumes the preselect once per tab (#146342)", () => {
  const consumedKey = "hermes.bootstrap-profile.consumed";

  function stubSessionStorage(store: Record<string, string> = {}) {
    vi.stubGlobal("sessionStorage", {
      getItem: (key: string) => (key in store ? store[key] : null),
      setItem: (key: string, value: string) => {
        store[key] = value;
      },
    });
    return store;
  }

  const loadFresh = () => {
    vi.resetModules();
    return import("./profile-bootstrap");
  };

  it("seeds the scope from the preselect on the first load and marks it consumed", async () => {
    const store = stubSessionStorage();
    vi.stubGlobal("window", { __HERMES_INITIAL_PROFILE__: "code-monkey" });
    const mod = await loadFresh();
    expect(mod.dashboardInitialProfile()).toBe("code-monkey");
    expect(store[consumedKey]).toBe("1");
  });

  it("drops the preselect on a later reload in the same tab", async () => {
    stubSessionStorage({ [consumedKey]: "1" });
    vi.stubGlobal("window", { __HERMES_INITIAL_PROFILE__: "code-monkey" });
    const mod = await loadFresh();
    expect(mod.dashboardInitialProfile()).toBe("");
  });

  it("marks nothing when the server injected no preselect", async () => {
    const store = stubSessionStorage();
    vi.stubGlobal("window", {});
    const mod = await loadFresh();
    expect(mod.dashboardInitialProfile()).toBe("");
    expect(consumedKey in store).toBe(false);
  });

  it("still honors the preselect when sessionStorage is unavailable", async () => {
    vi.stubGlobal("window", { __HERMES_INITIAL_PROFILE__: "code-monkey" });
    vi.stubGlobal("sessionStorage", {
      getItem: () => {
        throw new Error("storage disabled");
      },
      setItem: () => {
        throw new Error("storage disabled");
      },
    });
    const mod = await loadFresh();
    expect(mod.dashboardInitialProfile()).toBe("code-monkey");
  });

  it("a model-switch reload with default selected stays on default", async () => {
    // The issue's exact sequence: launch preselect "code-monkey", the user
    // switches the scope to the dashboard's own profile (spelled as a
    // profile-less URL), then confirms the model-switch reload. With the
    // preselect consumed, the profile-less URL resolves to the serving
    // profile instead of re-seeding "code-monkey".
    stubSessionStorage({ [consumedKey]: "1" });
    vi.stubGlobal("window", {
      __HERMES_INITIAL_PROFILE__: "code-monkey",
      __HERMES_DASHBOARD_PROFILE__: "default",
    });
    const mod = await loadFresh();
    expect(mod.initialProfileScope(new URLSearchParams(""))).toBe("default");
  });
});
