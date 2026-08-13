import { enableAutoUnmount, mount } from "@vue/test-utils";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { nextTick } from "vue";
import { createMemoryHistory, createRouter } from "vue-router";

import App from "../App.vue";
import ChatView from "../views/ChatView.vue";
import ExcelView from "../views/ExcelView.vue";
import EmployeeStatus from "../components/EmployeeStatus.vue";

enableAutoUnmount(afterEach);

const createTestRouter = (initialPath = "/chat") => {
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: "/", redirect: "/chat" },
      { path: "/chat", name: "chat", component: ChatView, meta: { title: "长期对话" } },
      { path: "/excel", name: "excel", component: ExcelView, meta: { title: "Listing 表格" } },
    ],
  });
  void router.push(initialPath);
  return router;
};

const setViewport = (width: number) => {
  Object.defineProperty(window, "innerWidth", { configurable: true, value: width });
  window.dispatchEvent(new Event("resize"));
};

describe("WorkspaceLayout", () => {
  beforeEach(() => {
    localStorage.clear();
    setViewport(1440);
  });

  afterEach(() => {
    document.body.style.overflow = "";
    localStorage.clear();
    setViewport(1440);
  });

  it("renders exactly two primary navigation destinations and marks the active route", async () => {
    const router = createTestRouter("/chat");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });

    const links = wrapper.findAll('[data-testid="primary-navigation"] a');
    expect(links).toHaveLength(2);
    expect(links.map((link) => link.text())).toEqual(["长期对话", "Listing 表格"]);
    expect(links[0].attributes("aria-current")).toBe("page");
    expect(wrapper.find("h1").text()).toBe("长期对话");

    wrapper.unmount();
  });

  it("collapses the desktop sidebar and persists the preference", async () => {
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    await wrapper.get('[aria-label="收起侧边栏"]').trigger("click");

    expect(wrapper.get("[data-workspace]").classes()).toContain("is-sidebar-collapsed");
    expect(localStorage.getItem("etsy-workspace-sidebar-collapsed")).toBe("true");
    expect(wrapper.find('[aria-label="展开侧边栏"]').exists()).toBe(true);
  });

  it("recovers when the persisted sidebar preference is invalid", async () => {
    localStorage.setItem("etsy-workspace-sidebar-collapsed", "definitely");
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    expect(wrapper.get("[data-workspace]").classes()).not.toContain("is-sidebar-collapsed");
  });

  it("opens an accessible mobile drawer and closes it with Escape", async () => {
    setViewport(390);
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
    await nextTick();
    const menu = wrapper.get('[aria-label="打开导航菜单"]');
    const sidebar = wrapper.get("#workspace-navigation");

    expect(menu.attributes("aria-expanded")).toBe("false");
    expect(menu.attributes("aria-controls")).toBe("workspace-navigation");
    expect(sidebar.attributes()).toHaveProperty("inert");
    expect(sidebar.findAll("a, button").every((item) => item.attributes("tabindex") === "-1")).toBe(true);
    await menu.trigger("click");
    expect(sidebar.attributes("aria-hidden")).toBe("false");
    expect(sidebar.attributes()).not.toHaveProperty("inert");
    expect(document.activeElement).toBe(sidebar.get("a").element);
    expect(wrapper.get(".workspace__stage").attributes()).toHaveProperty("inert");
    expect(document.body.style.overflow).toBe("hidden");

    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await nextTick();
    expect(sidebar.attributes("aria-hidden")).toBe("true");
    expect(sidebar.attributes()).toHaveProperty("inert");
    expect(wrapper.get(".workspace__stage").attributes()).not.toHaveProperty("inert");
    expect(document.activeElement).toBe(menu.element);
    expect(document.body.style.overflow).toBe("");

    wrapper.unmount();
  });

  it("does not remove desktop sidebar controls from the tab order", async () => {
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    const sidebar = wrapper.get("#workspace-navigation");
    expect(sidebar.attributes()).not.toHaveProperty("inert");
    expect(sidebar.findAll("a, button").every((item) => item.attributes("tabindex") !== "-1")).toBe(true);
  });

  it("keeps keyboard focus inside the open mobile drawer", async () => {
    setViewport(390);
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
    await nextTick();

    await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    const sidebar = wrapper.get("#workspace-navigation");
    const first = sidebar.get<HTMLElement>('[aria-label="关闭导航菜单"]').element;
    const last = sidebar.get<HTMLElement>('a[href="/excel"]').element;

    last.focus();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", bubbles: true }));
    expect(document.activeElement).toBe(first);

    first.focus();
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Tab", shiftKey: true, bubbles: true }));
    expect(document.activeElement).toBe(last);

  });

  it("restores an existing body overflow style without overwriting later external changes", async () => {
    setViewport(390);
    document.body.style.overflow = "clip";
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
    await nextTick();

    await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    expect(document.body.style.overflow).toBe("hidden");
    await wrapper.get('[data-testid="drawer-backdrop"]').trigger("click");
    expect(document.body.style.overflow).toBe("clip");

    await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    document.body.style.overflow = "scroll";
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    await nextTick();
    expect(document.body.style.overflow).toBe("scroll");
  });

  it("restores the original body overflow when unmounted while the drawer is open", async () => {
    setViewport(390);
    document.body.style.overflow = "clip";
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
    await nextTick();

    await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    wrapper.unmount();

    expect(document.body.style.overflow).toBe("clip");
  });

  it("removes its global event listeners when unmounted", async () => {
    const addSpy = vi.spyOn(window, "addEventListener");
    const removeSpy = vi.spyOn(window, "removeEventListener");
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    const resizeHandler = addSpy.mock.calls.find(([type]) => type === "resize")?.[1];
    const keydownHandler = addSpy.mock.calls.find(([type]) => type === "keydown")?.[1];
    wrapper.unmount();

    expect(removeSpy).toHaveBeenCalledWith("resize", resizeHandler);
    expect(removeSpy).toHaveBeenCalledWith("keydown", keydownHandler);
    addSpy.mockRestore();
    removeSpy.mockRestore();
  });

  it("closes the mobile drawer by backdrop and after route navigation", async () => {
    setViewport(390);
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
    await nextTick();

    await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    await wrapper.get('[data-testid="drawer-backdrop"]').trigger("click");
    expect(wrapper.get("#workspace-navigation").attributes("aria-hidden")).toBe("true");

    await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    const navigated = new Promise<void>((resolve) => router.afterEach(() => resolve()));
    await wrapper.get('a[href="/excel"]').trigger("click");
    await navigated;
    await nextTick();
    expect(wrapper.get("#workspace-navigation").attributes("aria-hidden")).toBe("true");
    expect(wrapper.find("h1").text()).toBe("Listing 表格");

    wrapper.unmount();
  });

  it.each([1440, 390])("focuses the page heading and updates the document title after navigation at %ipx", async (width) => {
    setViewport(width);
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] }, attachTo: document.body });
    await nextTick();
    const initialHeading = wrapper.get<HTMLElement>("h1").element;
    expect(document.activeElement).not.toBe(initialHeading);

    if (width === 390) await wrapper.get('[aria-label="打开导航菜单"]').trigger("click");
    const navigated = new Promise<void>((resolve) => router.afterEach(() => resolve()));
    await router.push("/excel");
    await navigated;
    await nextTick();

    expect(document.activeElement).toBe(wrapper.get("h1").element);
    expect(document.title).toBe("Listing 表格 · Etsy 表演服数字员工");
    if (width === 390) {
      expect(wrapper.get("#workspace-navigation").attributes()).toHaveProperty("inert");
      expect(document.activeElement).not.toBe(wrapper.get('[aria-label="打开导航菜单"]').element);
    }
  });

  it("exposes only one live employee status in the workspace", async () => {
    const router = createTestRouter();
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    expect(wrapper.findAll('[role="status"][aria-live="polite"]')).toHaveLength(1);
    expect(wrapper.findAll('[aria-label^="数字员工状态"]')).toHaveLength(2);
  });
});

describe("EmployeeStatus", () => {
  it.each([
    ["online", "在线"],
    ["busy", "工作中"],
    ["offline", "离线"],
    ["error", "异常"],
  ] as const)("renders the %s state with a semantic label", (status, label) => {
    const wrapper = mount(EmployeeStatus, { props: { status, announce: true } });

    expect(wrapper.attributes("role")).toBe("status");
    expect(wrapper.attributes("aria-live")).toBe("polite");
    expect(wrapper.text()).toContain(label);
    expect(wrapper.get("[data-status-dot]").attributes("aria-hidden")).toBe("true");
  });

  it.each([
    ["online", "在"],
    ["busy", "忙"],
    ["offline", "离"],
    ["error", "错"],
  ] as const)("renders the %s state visibly in compact mode", (status, shortLabel) => {
    const wrapper = mount(EmployeeStatus, { props: { status, compact: true } });

    expect(wrapper.get("[data-status-short]").text()).toBe(shortLabel);
    expect(wrapper.get("[data-status-short]").classes()).not.toContain("sr-only");
  });
});
