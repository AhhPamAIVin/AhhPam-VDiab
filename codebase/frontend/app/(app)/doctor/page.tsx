'use client'

import { useRouter } from 'next/navigation'
import { DoctorDashboard } from '@/components/pages/doctor-dashboard'
import { DoctorAntdTheme } from '@/components/doctor-antd-theme'

export default function Page() {
  const router = useRouter()
  return (
    <DoctorAntdTheme><DoctorDashboard
      onViewPatient={(id) => router.push(`/doctor/patients/${id}`)}
    /></DoctorAntdTheme>
  )
}
