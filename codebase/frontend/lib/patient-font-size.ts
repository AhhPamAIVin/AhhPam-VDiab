export const PATIENT_FONT_SIZE_KEY = 'vdiab-patient-font-size'

export type PatientFontSize = 'standard' | 'large' | 'extra-large'

export const PATIENT_FONT_SIZES: Record<PatientFontSize, string> = {
  standard: '16px',
  large: '18px',
  'extra-large': '20px',
}

export function getPatientFontSize(): PatientFontSize {
  if (typeof window === 'undefined') return 'standard'
  const saved = window.localStorage.getItem(PATIENT_FONT_SIZE_KEY)
  return saved === 'large' || saved === 'extra-large' ? saved : 'standard'
}

export function applyPatientFontSize(size: PatientFontSize) {
  document.documentElement.style.fontSize = PATIENT_FONT_SIZES[size]
}

export function savePatientFontSize(size: PatientFontSize) {
  applyPatientFontSize(size)
  window.localStorage.setItem(PATIENT_FONT_SIZE_KEY, size)
}
