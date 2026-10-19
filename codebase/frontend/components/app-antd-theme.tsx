"use client";

import { useEffect, useState } from "react";
import { ConfigProvider, theme as antdTheme } from "antd";

function documentIsDark() {
  return typeof document !== "undefined" && document.documentElement.classList.contains("dark");
}

export function AppAntdTheme({ children }: { children: React.ReactNode }) {
  const [dark, setDark] = useState(false);

  useEffect(() => {
    const root = document.documentElement;
    const syncTheme = () => setDark(documentIsDark());
    syncTheme();

    const observer = new MutationObserver(syncTheme);
    observer.observe(root, { attributes: true, attributeFilter: ["class"] });
    return () => observer.disconnect();
  }, []);

  return (
    <ConfigProvider
      theme={{
        algorithm: dark ? antdTheme.darkAlgorithm : antdTheme.defaultAlgorithm,
        token: {
          colorPrimary: "#43B5AE",
          colorInfo: "#43B5AE",
          colorLink: dark ? "#82C7C2" : "#2D9D97",
          colorBgBase: dark ? "#10191B" : "#FFFFFF",
          colorBgContainer: dark ? "#172225" : "#FFFFFF",
          colorBgElevated: dark ? "#1B292C" : "#FFFFFF",
          colorBorder: dark ? "#314347" : "#E1E8E7",
          colorText: dark ? "#DCE9E8" : "#172322",
          colorTextSecondary: dark ? "#9FB2B0" : "#667573",
          borderRadius: 12,
          controlHeightLG: 42,
        },
        components: {
          DatePicker: {
            activeBorderColor: "#43B5AE",
            hoverBorderColor: "#43B5AE",
          },
          Select: {
            activeBorderColor: "#43B5AE",
            hoverBorderColor: "#43B5AE",
            optionSelectedBg: dark ? "#21413F" : "#DFF7F3",
            optionSelectedColor: dark ? "#A4D8D4" : "#238D87",
          },
        },
      }}
    >
      {children}
    </ConfigProvider>
  );
}
