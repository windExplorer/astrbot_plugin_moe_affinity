// Naive UI 主题：与 styles.css 里的粉色/紫色强调色保持一致。
// 组件在 main.ts 里全局注册（kebab-case 使用），避免每个文件重复 import。

import { darkTheme, dateZhCN, zhCN } from "naive-ui";

export { darkTheme, dateZhCN, zhCN };

export const themeOverrides = {
  common: {
    primaryColor: "#f2789f",
    primaryColorHover: "#f58cae",
    primaryColorPressed: "#e0668d",
    primaryColorSuppl: "#8b7cf6",
    borderRadius: "10px",
    borderRadiusSmall: "8px",
    fontSize: "13px",
    heightMedium: "34px",
    heightSmall: "28px",
  },
  Button: {
    borderRadiusMedium: "10px",
    borderRadiusSmall: "8px",
    fontWeightText: "500",
    fontWeight: "600",
  },
  Input: {
    borderRadius: "10px",
  },
  Card: {
    borderRadius: "14px",
  },
  Tag: {
    borderRadius: "8px",
  },
  DataTable: {
    borderRadius: "12px",
  },
};
