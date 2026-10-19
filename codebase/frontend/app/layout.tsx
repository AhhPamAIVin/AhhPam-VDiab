import type { Metadata, Viewport } from 'next'
import './globals.css'
import { AuthProvider } from '@/lib/auth-context'
import { ToastProvider } from '@/components/ui/toast'
import { AppAntdTheme } from '@/components/app-antd-theme'

export const metadata: Metadata = {
  title: 'VDiab – Diabetes Health Management',
  description: 'Hệ thống hỗ trợ bệnh nhân đái tháo đường theo dõi sức khỏe, quản lý dữ liệu hằng ngày, nhận cảnh báo và trao đổi với trợ lý AI.',
  icons: {
    icon: [{ url: '/icon.svg', type: 'image/svg+xml' }],
    shortcut: '/icon.svg',
    apple: '/apple-icon.png',
  },
}

export const viewport: Viewport = {
  colorScheme: 'light dark',
  themeColor: '#43B5AE',
  width: 'device-width',
  initialScale: 1,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="vi" className="bg-background">
      <body className="antialiased font-sans">
        <AuthProvider>
          <AppAntdTheme>
            <ToastProvider>
              {children}
            </ToastProvider>
          </AppAntdTheme>
        </AuthProvider>
      </body>
    </html>
  )
}
