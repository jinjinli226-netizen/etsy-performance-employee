import { mount } from "@vue/test-utils";
import { describe, expect, it } from "vitest";

import App from "./App.vue";

describe("App", () => {
  it("renders the two employee capability labels", () => {
    const wrapper = mount(App);

    expect(wrapper.findAll("li").map((item) => item.text())).toEqual([
      "Excel 自动化",
      "长期对话",
    ]);
  });
});
