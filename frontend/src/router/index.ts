import { createRouter, createWebHistory } from "vue-router";

import ChatView from "../views/ChatView.vue";
import ExcelView from "../views/ExcelView.vue";

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: "/", redirect: "/chat" },
    {
      path: "/chat",
      name: "chat",
      component: ChatView,
      meta: { title: "长期对话" },
    },
    {
      path: "/excel",
      name: "excel",
      component: ExcelView,
      meta: { title: "Listing 表格" },
    },
  ],
});

export default router;
