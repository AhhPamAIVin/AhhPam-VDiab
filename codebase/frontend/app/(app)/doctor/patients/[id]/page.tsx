'use client'

import { use } from 'react'
import { useRouter } from 'next/navigation'
import { PatientDetailPage } from '@/components/pages/patient-detail-page'
import { DoctorAntdTheme } from '@/components/doctor-antd-theme'

export default function Page({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params)
  const router = useRouter()
  return (
    <DoctorAntdTheme><PatientDetailPage
      patientId={id}
      onBack={() => router.push('/doctor')}
    /></DoctorAntdTheme>
  )
}
