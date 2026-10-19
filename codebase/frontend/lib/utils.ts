import { clsx, type ClassValue } from 'clsx'
import { twMerge } from 'tailwind-merge'

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}
export function parseUtcTimestamp(value: string) {
  const hasTimezone = /(?:[zZ]|[+-]\d{2}:\d{2})$/.test(value)
  return new Date(hasTimezone ? value : `${value}Z`)
}
