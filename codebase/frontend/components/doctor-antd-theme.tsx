"use client";

import { ConfigProvider } from "antd";

export function DoctorAntdTheme({ children }: { children: React.ReactNode }) {
  return (
    <ConfigProvider
      theme={{
        token: {
          colorPrimary: "#43B5AE",
          colorInfo: "#43B5AE",
          colorLink: "#2D9D97",
          colorLinkHover: "#238D87",
          borderRadius: 12,
          controlHeightLG: 42,
          colorBorder: "#E1E8E7",
          colorPrimaryHover: "#2D9D97",
          colorPrimaryActive: "#238D87",
          boxShadowSecondary: "0 10px 30px rgba(45, 157, 151, 0.14)",
        },
        components: {
          DatePicker: {
            activeBorderColor: "#43B5AE",
            hoverBorderColor: "#43B5AE",
          },
          Select: {
            activeBorderColor: "#43B5AE",
            hoverBorderColor: "#43B5AE",
            optionSelectedBg: "#DFF7F3",
            optionSelectedColor: "#238D87",
            optionSelectedFontWeight: 400,
          },
        },
      }}
    >
      <div className="doctor-antd-theme">{children}</div>
      <style jsx global>{`
        .doctor-antd-theme .ant-select-selection-item,
        .doctor-antd-theme .ant-select-selection-placeholder {
          font-size: 13px;
          font-weight: 400;
        }
        .ant-select-dropdown .ant-select-item-option-content {
          font-size: 13px;
          font-weight: 400;
        }
      `}</style>
    </ConfigProvider>
  );
}
