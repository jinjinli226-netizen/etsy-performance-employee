import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";
import { createMemoryHistory, createRouter } from "vue-router";

import App from "./App.vue";
import ChatView from "./views/ChatView.vue";
import ExcelView from "./views/ExcelView.vue";

describe("App", () => {
  it("renders the two employee capability labels", async () => {
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: "/chat", component: ChatView, meta: { title: "长期对话" } },
        { path: "/excel", component: ExcelView, meta: { title: "Listing 表格" } },
      ],
    });
    await router.push("/chat");
    await router.isReady();
    const wrapper = mount(App, { global: { plugins: [router] } });

    expect(wrapper.findAll('[data-testid="primary-navigation"] a').map((item) => item.text())).toEqual([
      "长期对话",
      "Listing 表格",
    ]);
  });
});
