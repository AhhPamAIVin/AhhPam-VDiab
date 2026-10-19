// ============================================================
// VDiab Mock Data
// ============================================================

export const mockPatient = {
  id: 'p1',
  name: 'Nguyễn Văn An',
  age: 58,
  gender: 'Nam',
  dob: '1966-03-15',
  disease: 'Đái tháo đường type 2',
  height: 168,
  weight: 68.5,
  bmi: 24.3,
  doctor: 'BS. Trần Minh',
  phone: '0912 345 678',
  email: 'an.nguyen@email.com',
  address: '123 Nguyễn Trãi, Quận 1, TP.HCM',
  emergencyContact: 'Nguyễn Thị Bích – 0987 654 321',
  targetGlucoseMin: 70,
  targetGlucoseMax: 140,
  alertGlucoseHigh: 180,
  alertGlucoseLow: 60,
  avatar: null,
}

export const glucoseData7Days = [
  { date: '28/7', morning: 118, afternoon: 145, evening: 132, night: 125 },
  { date: '29/7', morning: 122, afternoon: 138, evening: 128, night: 119 },
  { date: '30/7', morning: 135, afternoon: 152, evening: 141, night: 130 },
  { date: '31/7', morning: 142, afternoon: 165, evening: 148, night: 136 },
  { date: '1/8', morning: 148, afternoon: 158, evening: 155, night: 140 },
  { date: '2/8', morning: 152, afternoon: 162, evening: 149, night: 138 },
  { date: '3/8', morning: 126, afternoon: 143, evening: 138, night: 124 },
]

export const glucoseData30Days = Array.from({ length: 30 }, (_, i) => {
  const d = new Date(2026, 6, 4 + i)
  return {
    date: `${d.getDate()}/${d.getMonth() + 1}`,
    value: Math.floor(110 + Math.random() * 80),
  }
})

export const bpData7Days = [
  { date: '28/7', systolic: 128, diastolic: 82 },
  { date: '29/7', systolic: 122, diastolic: 78 },
  { date: '30/7', systolic: 135, diastolic: 85 },
  { date: '31/7', systolic: 130, diastolic: 83 },
  { date: '1/8', systolic: 126, diastolic: 80 },
  { date: '2/8', systolic: 124, diastolic: 79 },
  { date: '3/8', systolic: 125, diastolic: 80 },
]

export const weightData7Days = [
  { date: '28/7', value: 69.2 },
  { date: '29/7', value: 69.0 },
  { date: '30/7', value: 68.8 },
  { date: '31/7', value: 68.9 },
  { date: '1/8', value: 68.6 },
  { date: '2/8', value: 68.7 },
  { date: '3/8', value: 68.5 },
]

export const glucoseHistory = [
  { id: 'g1', date: '03/08/2026', time: '07:15', value: 126, timing: 'Lúc đói', note: '', status: 'normal' },
  { id: 'g2', date: '03/08/2026', time: '13:30', value: 143, timing: 'Sau ăn', note: 'Ăn cơm trưa nhiều', status: 'normal' },
  { id: 'g3', date: '02/08/2026', time: '07:10', value: 152, timing: 'Lúc đói', note: '', status: 'high' },
  { id: 'g4', date: '02/08/2026', time: '19:45', value: 149, timing: 'Sau ăn', note: '', status: 'normal' },
  { id: 'g5', date: '01/08/2026', time: '07:05', value: 148, timing: 'Lúc đói', note: 'Ngủ không ngon', status: 'high' },
  { id: 'g6', date: '01/08/2026', time: '12:00', value: 158, timing: 'Sau ăn', note: '', status: 'high' },
  { id: 'g7', date: '31/07/2026', time: '07:20', value: 142, timing: 'Lúc đói', note: '', status: 'normal' },
  { id: 'g8', date: '31/07/2026', time: '245', value: 245, timing: 'Sau ăn', note: 'Ăn nhiều tinh bột', status: 'critical' },
]

export const medications = [
  {
    id: 'm1',
    name: 'Metformin',
    dose: '500 mg',
    frequency: '2 lần/ngày',
    timing: ['Sáng', 'Tối'],
    prescribedBy: 'BS. Trần Minh',
    startDate: '01/01/2025',
    status: 'active',
    taken: { morning: true, evening: false },
    instruction: 'Uống sau bữa ăn. Không nhai, nuốt nguyên viên.',
  },
  {
    id: 'm2',
    name: 'Atorvastatin',
    dose: '10 mg',
    frequency: '1 lần/ngày',
    timing: ['Tối'],
    prescribedBy: 'BS. Trần Minh',
    startDate: '15/03/2025',
    status: 'active',
    taken: { morning: false, evening: false },
    instruction: 'Uống vào buổi tối trước khi ngủ.',
  },
]

export const meals = [
  {
    id: 'meal1',
    date: '03/08/2026',
    time: '07:30',
    type: 'Bữa sáng',
    name: 'Cháo yến mạch với trứng luộc',
    carbs: 42,
    protein: 18,
    fat: 8,
    fiber: 5,
    suitability: 'good',
    note: '',
    warning: '',
  },
  {
    id: 'meal2',
    date: '03/08/2026',
    time: '12:00',
    type: 'Bữa trưa',
    name: 'Cơm gạo lứt, cá hồi, rau luộc',
    carbs: 65,
    protein: 28,
    fat: 12,
    fiber: 8,
    suitability: 'good',
    note: '',
    warning: 'Lượng carbs ở mức vừa phải',
  },
  {
    id: 'meal3',
    date: '02/08/2026',
    time: '19:30',
    type: 'Bữa tối',
    name: 'Bún bò huế',
    carbs: 95,
    protein: 22,
    fat: 15,
    fiber: 3,
    suitability: 'caution',
    note: 'Nhiều tinh bột',
    warning: 'Lượng carbs cao, có thể ảnh hưởng đường huyết',
  },
]

export const notifications = [
  {
    id: 'n1',
    type: 'critical',
    title: 'Cảnh báo đường huyết cao',
    message: 'Đường huyết 245 mg/dL lúc 19:45, cao hơn ngưỡng bác sĩ đã thiết lập (180 mg/dL).',
    time: '19:45 – 31/07/2026',
    read: false,
    category: 'alert',
  },
  {
    id: 'n2',
    type: 'warning',
    title: 'Nhắc uống thuốc',
    message: 'Đã đến giờ uống Metformin 500mg buổi tối. Đừng quên nhé!',
    time: '20:00 – 03/08/2026',
    read: false,
    category: 'reminder',
  },
  {
    id: 'n3',
    type: 'info',
    title: 'Nhắc đo đường huyết',
    message: 'Hãy đo đường huyết lúc đói trước bữa sáng để theo dõi chính xác hơn.',
    time: '06:30 – 03/08/2026',
    read: true,
    category: 'reminder',
  },
  {
    id: 'n4',
    type: 'info',
    title: 'Thông báo từ bác sĩ',
    message: 'BS. Trần Minh đã xem lại hồ sơ của bạn và đặt lịch tái khám vào ngày 15/08/2026.',
    time: '14:00 – 02/08/2026',
    read: true,
    category: 'doctor',
  },
  {
    id: 'n5',
    type: 'warning',
    title: 'Nhắc vận động',
    message: 'Hôm nay bạn chưa đạt mục tiêu 6.000 bước. Hãy đi bộ thêm một chút nhé!',
    time: '18:00 – 02/08/2026',
    read: true,
    category: 'reminder',
  },
  {
    id: 'n6',
    type: 'info',
    title: 'Nhắc tái khám',
    message: 'Lịch tái khám sắp đến: 15/08/2026 lúc 09:00 tại Phòng khám Đa khoa Minh Tâm.',
    time: '08:00 – 01/08/2026',
    read: true,
    category: 'appointment',
  },
]

// Doctor mock data
export const mockDoctor = {
  id: 'd1',
  name: 'BS. Trần Minh',
  specialty: 'Nội tiết – Đái tháo đường',
  hospital: 'BV Đa Khoa Minh Tâm',
  avatar: null,
}

export const doctorPatients = [
  {
    id: 'p1',
    name: 'Nguyễn Văn An',
    age: 58,
    gender: 'Nam',
    lastGlucose: 152,
    lastGlucoseTime: '07:10 – 02/08',
    alertLevel: 'warning',
    alertMessage: 'Đường huyết đói liên tục > 140 mg/dL trong 3 ngày',
    updatedAt: '2 giờ trước',
    disease: 'ĐTĐ type 2',
  },
  {
    id: 'p2',
    name: 'Lê Thị Hoa',
    age: 65,
    gender: 'Nữ',
    lastGlucose: 245,
    lastGlucoseTime: '19:45 – 31/07',
    alertLevel: 'critical',
    alertMessage: 'Đường huyết 245 mg/dL, vượt ngưỡng cảnh báo nghiêm trọng',
    updatedAt: '1 ngày trước',
    disease: 'ĐTĐ type 2',
  },
  {
    id: 'p3',
    name: 'Trần Văn Bình',
    age: 52,
    gender: 'Nam',
    lastGlucose: 118,
    lastGlucoseTime: '08:00 – 03/08',
    alertLevel: 'normal',
    alertMessage: '',
    updatedAt: '3 giờ trước',
    disease: 'ĐTĐ type 2',
  },
  {
    id: 'p4',
    name: 'Phạm Thị Lan',
    age: 70,
    gender: 'Nữ',
    lastGlucose: 98,
    lastGlucoseTime: '07:30 – 03/08',
    alertLevel: 'normal',
    alertMessage: '',
    updatedAt: '4 giờ trước',
    disease: 'ĐTĐ type 1',
  },
  {
    id: 'p5',
    name: 'Võ Văn Tùng',
    age: 61,
    gender: 'Nam',
    lastGlucose: 175,
    lastGlucoseTime: '14:00 – 02/08',
    alertLevel: 'warning',
    alertMessage: 'Đường huyết sau ăn cao, cần điều chỉnh chế độ ăn',
    updatedAt: '5 giờ trước',
    disease: 'ĐTĐ type 2',
  },
]

// Temporary UI-only content until a Doctor AI summary endpoint is available.
export const doctorAiSummary = {
  currentCondition: 'Đường huyết gần đây cần được theo dõi theo lịch đo của bệnh nhân.',
  importantTrends: 'Theo dõi các lần đo bất thường và đối chiếu với thời điểm dùng thuốc, bữa ăn.',
  pointsToReview: 'Trao đổi với bệnh nhân về các chỉ số bất thường trong lần tái khám tiếp theo.',
}
export const doctorAppointments = [
  { id: 'a1', patient: 'Nguyễn Văn An', time: '09:00', date: '03/08/2026', type: 'Tái khám định kỳ' },
  { id: 'a2', patient: 'Lê Thị Hoa', time: '10:30', date: '03/08/2026', type: 'Khám cấp cứu' },
  { id: 'a3', patient: 'Phạm Thị Lan', time: '14:00', date: '03/08/2026', type: 'Tư vấn dinh dưỡng' },
  { id: 'a4', patient: 'Trần Văn Bình', time: '09:00', date: '05/08/2026', type: 'Tái khám định kỳ' },
]

// Admin mock data
export const adminUsers = [
  { id: 'u1', name: 'Nguyễn Văn An', email: 'an.nguyen@email.com', role: 'patient', status: 'active', createdAt: '01/01/2025', lastLogin: '03/08/2026' },
  { id: 'u2', name: 'Lê Thị Hoa', email: 'hoa.le@email.com', role: 'patient', status: 'active', createdAt: '15/02/2025', lastLogin: '02/08/2026' },
  { id: 'u3', name: 'Trần Văn Bình', email: 'binh.tran@email.com', role: 'patient', status: 'active', createdAt: '20/03/2025', lastLogin: '03/08/2026' },
  { id: 'u4', name: 'BS. Trần Minh', email: 'minh.tran@hospital.com', role: 'doctor', status: 'active', createdAt: '01/01/2025', lastLogin: '03/08/2026' },
  { id: 'u5', name: 'BS. Nguyễn Thu', email: 'thu.nguyen@hospital.com', role: 'doctor', status: 'active', createdAt: '01/01/2025', lastLogin: '01/08/2026' },
  { id: 'u6', name: 'Admin Hệ Thống', email: 'admin@vdiab.vn', role: 'admin', status: 'active', createdAt: '01/01/2025', lastLogin: '03/08/2026' },
  { id: 'u7', name: 'Phạm Thị Lan', email: 'lan.pham@email.com', role: 'patient', status: 'locked', createdAt: '10/04/2025', lastLogin: '20/07/2026' },
  { id: 'u8', name: 'Võ Văn Tùng', email: 'tung.vo@email.com', role: 'patient', status: 'active', createdAt: '05/05/2025', lastLogin: '02/08/2026' },
]

export const aiMessages = [
  {
    id: 'ai1',
    role: 'assistant' as const,
    content: 'Xin chào! Tôi là trợ lý sức khỏe VDiab. Tôi có thể giúp bạn giải thích các chỉ số sức khỏe, tóm tắt dữ liệu và trả lời câu hỏi về bệnh đái tháo đường. Hôm nay bạn cần hỗ trợ gì?',
    time: '09:00',
  },
]
