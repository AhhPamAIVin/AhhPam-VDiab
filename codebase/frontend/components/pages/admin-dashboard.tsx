"use client";

import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";
import {
  CheckCircle2,
  ClipboardList,
  Eye,
  EyeOff,
  KeyRound,
  Link2,
  Loader2,
  Lock,
  MoreVertical,
  Plus,
  RefreshCw,
  Search,
  Settings,
  ShieldCheck,
  Stethoscope,
  Trash2,
  Unlink,
  UserCog,
  UserPlus,
  Users,
  X,
  XCircle,
} from "lucide-react";
import { api, ApiUser, PatientAssignment, UserRole } from "@/lib/api";
import { useToast } from "@/components/ui/toast";
import { cn, parseUtcTimestamp } from "@/lib/utils";
import { useRouter, useSearchParams } from "next/navigation";
import { Pagination } from "@/components/ui/pagination";
import { AdminAuditPanel } from "@/components/admin-audit-panel";
import { AdminDeletionRequests } from "@/components/admin-deletion-requests";

const PAGE_SIZE = 20;

type AdminTab = "overview" | "users" | "assignments" | "audit" | "settings";
type RoleFilter = "all" | UserRole;
type StatusFilter = "all" | "active" | "locked";
import { ConfigProvider, Select } from "antd";

const roleConfig = {
  patient: {
    label: "Bệnh nhân",
    icon: Users,
    style: "bg-blue-50 text-blue-700 border-blue-200",
  },
  doctor: {
    label: "Bác sĩ",
    icon: Stethoscope,
    style: "bg-emerald-50 text-emerald-700 border-emerald-200",
  },
  admin: {
    label: "Quản trị",
    icon: ShieldCheck,
    style: "bg-purple-50 text-purple-700 border-purple-200",
  },
};

function Modal({
  title,
  onClose,
  children,
}: {
  title: string;
  onClose: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
      <button
        aria-label="Đóng"
        className="absolute inset-0 bg-slate-950/45 backdrop-blur-[2px]"
        onClick={onClose}
      />
      <section className="relative w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-2xl">
        <div className="mb-5 flex items-center justify-between">
          <h3 className="text-lg font-bold text-foreground">{title}</h3>
          <button
            onClick={onClose}
            className="rounded-lg p-2 text-muted-foreground hover:bg-muted"
          >
            <X className="h-4 w-4" />
          </button>
        </div>
        {children}
      </section>
    </div>
  );
}

export function AdminDashboard() {
  const { showToast } = useToast();
  const router = useRouter();
  const searchParams = useSearchParams();
  const [activeTab, setActiveTab] = useState<AdminTab>("overview");
  const [users, setUsers] = useState<ApiUser[]>([]);
  const [assignments, setAssignments] = useState<PatientAssignment[]>([]);
  const [loading, setLoading] = useState(true);
  const [search, setSearch] = useState("");
  const [roleFilter, setRoleFilter] = useState<RoleFilter>("all");
  const [statusFilter, setStatusFilter] = useState<StatusFilter>("all");
  const [showAddUser, setShowAddUser] = useState(false);
  const [showAssign, setShowAssign] = useState(false);
  const [resetUser, setResetUser] = useState<ApiUser | null>(null);
  const [openMenu, setOpenMenu] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [newUser, setNewUser] = useState({
    full_name: "",
    email: "",
    temporary_password: "",
    role: "patient" as UserRole,
  });
  const [assignmentForm, setAssignmentForm] = useState({
    doctor_id: "",
    patient_id: "",
  });
  const [resetPassword, setResetPassword] = useState("");
  const [generatedPassword, setGeneratedPassword] = useState("");
  const [showNewUserPassword, setShowNewUserPassword] = useState(false);
  const [settings, setSettings] = useState({
    emailAlerts: true,
    criticalAlerts: true,
    maintenance: false,
    autoBackup: true,
  });
  const [searchTerm, setSearchTerm] = useState("");
  const [assignmentDoctorFilter, setAssignmentDoctorFilter] = useState("all");
  const [userPage, setUserPage] = useState(1);
  const [assignmentPage, setAssignmentPage] = useState(1);

  const loadData = useCallback(async () => {
    setLoading(true);
    try {
      const [userData, assignmentData] = await Promise.all([
        api.users(),
        api.assignments(),
      ]);
      setUsers(userData);
      setAssignments(assignmentData);
    } catch (error) {
      showToast(
        error instanceof Error
          ? error.message
          : "Không thể tải dữ liệu quản trị",
        "error",
      );
    } finally {
      setLoading(false);
    }
  }, [showToast]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  useEffect(() => {
    const section = searchParams.get("section") || "overview";
    if (section === "doctors" || section === "patients") {
      setActiveTab("users");
      setRoleFilter(section === "doctors" ? "doctor" : "patient");
    } else if (
      ["overview", "users", "assignments", "audit", "settings"].includes(
        section,
      )
    ) {
      setActiveTab(section as AdminTab);
      if (section === "users") setRoleFilter("all");
    } else {
      setActiveTab("overview");
    }
  }, [searchParams]);

  const navigateTo = (section: string) => {
    router.push(
      section === "overview" ? "/admin" : `/admin?section=${section}`,
    );
  };

  const doctors = useMemo(
    () => users.filter((u) => u.role === "doctor" && u.is_active),
    [users],
  );
  const patients = useMemo(
    () => users.filter((u) => u.role === "patient" && u.is_active),
    [users],
  );
  const userById = useMemo(() => new Map(users.map((u) => [u.id, u])), [users]);
  const assignedPatientIds = useMemo(
    () => new Set(assignments.map((a) => a.patient_id)),
    [assignments],
  );
  const unassignedPatients = useMemo(
    () => patients.filter((patient) => !assignedPatientIds.has(patient.id)),
    [assignedPatientIds, patients],
  );
  const filteredUsers = useMemo(
    () =>
      users.filter((u) => {
        const query = search.trim().toLowerCase();
        return (
          (!query ||
            u.full_name.toLowerCase().includes(query) ||
            u.email.toLowerCase().includes(query)) &&
          (roleFilter === "all" || u.role === roleFilter) &&
          (statusFilter === "all" ||
            (statusFilter === "active" ? u.is_active : !u.is_active))
        );
      }),
    [users, search, roleFilter, statusFilter],
  );
  const paginatedUsers = filteredUsers.slice(
    (userPage - 1) * PAGE_SIZE,
    userPage * PAGE_SIZE,
  );

  useEffect(() => {
    setUserPage(1);
  }, [search, roleFilter, statusFilter]);

  useEffect(() => {
    setUserPage((current) =>
      Math.min(current, Math.max(1, Math.ceil(filteredUsers.length / PAGE_SIZE))),
    );
  }, [filteredUsers.length]);

  const createUser = async (event: FormEvent) => {
    event.preventDefault();
    setSaving(true);
    try {
      await api.createUser({
        ...newUser,
        full_name: newUser.full_name.trim(),
        email: newUser.email.trim(),
      });
      showToast("Đã tạo tài khoản thành công.", "success");
      setNewUser({
        full_name: "",
        email: "",
        temporary_password: "",
        role: "patient",
      });
      setShowAddUser(false);
      await loadData();
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Không thể tạo tài khoản",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  const assignPatient = async (event: FormEvent) => {
    event.preventDefault();
    if (!assignmentForm.doctor_id || !assignmentForm.patient_id) return;
    setSaving(true);
    try {
      await api.assignPatient(
        assignmentForm.doctor_id,
        assignmentForm.patient_id,
      );
      showToast("Đã gán bệnh nhân cho bác sĩ.", "success");
      setAssignmentForm({ doctor_id: "", patient_id: "" });
      setShowAssign(false);
      await loadData();
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Không thể tạo phân công",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  const unassign = async (assignment: PatientAssignment) => {
    if (!window.confirm("Bỏ phân công bệnh nhân này khỏi bác sĩ?")) return;
    try {
      await api.unassignPatient(assignment.id);
      setAssignments((items) =>
        items.filter((item) => item.id !== assignment.id),
      );
      showToast("Đã bỏ phân công.", "success");
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Không thể bỏ phân công",
        "error",
      );
    }
  };

  const handleResetPassword = async (event: FormEvent) => {
    event.preventDefault();
    if (!resetUser) return;
    setSaving(true);
    try {
      const result = await api.resetPassword(
        resetUser.id,
        resetPassword || undefined,
      );
      setGeneratedPassword(result.temporary_password);
      setResetPassword("");
      showToast(
        "Đã đặt lại mật khẩu và vô hiệu hóa phiên đăng nhập cũ.",
        "success",
      );
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Không thể đặt lại mật khẩu",
        "error",
      );
    } finally {
      setSaving(false);
    }
  };

  const staticAction = (message: string) =>
    showToast(`${message} (chế độ giao diện – backend chưa hỗ trợ).`, "info");

  const statCards = [
    {
      label: "Tổng tài khoản",
      value: users.length,
      sub: `${users.filter((u) => u.is_active).length} đang hoạt động`,
      icon: Users,
      color: "bg-teal-50 text-teal-700",
    },
    {
      label: "Bác sĩ",
      value: doctors.length,
      sub: `${assignments.length} lượt phân công`,
      icon: Stethoscope,
      color: "bg-emerald-50 text-emerald-700",
    },
    {
      label: "Bệnh nhân",
      value: patients.length,
      sub: `${patients.length - assignedPatientIds.size} chưa phân công`,
      icon: UserCog,
      color: "bg-blue-50 text-blue-700",
    },
    {
      label: "Quản trị viên",
      value: users.filter((u) => u.role === "admin").length,
      sub: "Toàn quyền hệ thống",
      icon: ShieldCheck,
      color: "bg-purple-50 text-purple-700",
    },
  ];

  const filteredAssignments = assignments.filter((a) => {
    const patient = userById.get(a.patient_id);
    const doctor = userById.get(a.doctor_id);
    const keyword = searchTerm.toLowerCase().trim();
    const matchesKeyword =
      !keyword ||
      patient?.full_name?.toLowerCase().includes(keyword) ||
      patient?.email?.toLowerCase().includes(keyword) ||
      doctor?.full_name?.toLowerCase().includes(keyword);
    const matchesDoctor =
      assignmentDoctorFilter === "all" ||
      a.doctor_id === assignmentDoctorFilter;
    return matchesKeyword && matchesDoctor;
  });
  const paginatedAssignments = filteredAssignments.slice(
    (assignmentPage - 1) * PAGE_SIZE,
    assignmentPage * PAGE_SIZE,
  );

  useEffect(() => {
    setAssignmentPage(1);
  }, [searchTerm, assignmentDoctorFilter]);

  useEffect(() => {
    setAssignmentPage((current) =>
      Math.min(
        current,
        Math.max(1, Math.ceil(filteredAssignments.length / PAGE_SIZE)),
      ),
    );
  }, [filteredAssignments.length]);

  return (
    <div className="w-full space-y-5">
      <header className="relative overflow-hidden rounded-3xl bg-gradient-to-br from-[#239d98] to-[#43b5ae] p-6 text-white shadow-sm">
        <div className="absolute -right-12 -top-16 h-48 w-48 rounded-full bg-white/10" />
        <div className="relative flex flex-col gap-5 md:flex-row md:items-center md:justify-between">
          <div>
            <p className="text-sm font-medium text-white/75">
              Trung tâm điều hành
            </p>
            <h1 className="mt-1 text-2xl font-bold">Quản trị hệ thống VDiab</h1>
            <p className="mt-1 text-sm text-white/75">
              Tài khoản, phân công chăm sóc và vận hành hệ thống tại một nơi.
            </p>
          </div>
          <div className="flex gap-2">
            <button
              onClick={loadData}
              className="flex items-center gap-2 rounded-xl bg-white/15 px-4 py-2.5 text-sm font-medium hover:bg-white/25"
            >
              <RefreshCw className={cn("h-4 w-4", loading && "animate-spin")} />{" "}
              Làm mới
            </button>
            <button
              onClick={() => {
                setShowNewUserPassword(false);
                setShowAddUser(true);
              }}
              className="flex items-center gap-2 rounded-xl bg-white px-4 py-2.5 text-sm font-semibold text-[#238f8a] hover:bg-white/90"
            >
              <UserPlus className="h-4 w-4" /> Tạo tài khoản
            </button>
          </div>
        </div>
      </header>

      {loading && users.length === 0 ? (
        <div className="flex h-56 items-center justify-center rounded-2xl border border-border bg-card">
          <Loader2 className="h-7 w-7 animate-spin text-primary" />
        </div>
      ) : null}

      {!loading && activeTab === "overview" && (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {statCards.map((card) => (
              <div
                key={card.label}
                className="rounded-2xl border border-border bg-card p-4"
              >
                <div
                  className={cn(
                    "mb-3 flex h-10 w-10 items-center justify-center rounded-xl",
                    card.color,
                  )}
                >
                  <card.icon className="h-5 w-5" />
                </div>
                <p className="text-2xl font-bold text-foreground">
                  {card.value}
                </p>
                <p className="text-sm font-semibold text-foreground">
                  {card.label}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">{card.sub}</p>
              </div>
            ))}
          </div>
          <div className="grid gap-4 lg:grid-cols-5">
            <section className="rounded-2xl border border-border bg-card p-5 lg:col-span-3">
              <div className="mb-4 flex items-center justify-between">
                <div>
                  <h2 className="font-semibold">Phân công gần đây</h2>
                  <p className="text-xs text-muted-foreground">
                    Dữ liệu trực tiếp từ hệ thống
                  </p>
                </div>
                <button
                  onClick={() => navigateTo("assignments")}
                  className="text-xs font-semibold text-primary"
                >
                  Xem tất cả
                </button>
              </div>
              <div className="space-y-2">
                {assignments.slice(0, 5).map((a) => (
                  <div
                    key={a.id}
                    className="flex items-center gap-3 rounded-xl bg-muted/60 p-3"
                  >
                    <div className="flex h-9 w-9 items-center justify-center rounded-xl bg-teal-50 text-primary">
                      <Link2 className="h-4 w-4" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="truncate text-sm font-medium">
                        {userById.get(a.patient_id)?.full_name || "Bệnh nhân"}
                      </p>
                      <p className="truncate text-xs text-muted-foreground">
                        Bác sĩ {userById.get(a.doctor_id)?.full_name || "—"}
                      </p>
                    </div>
                    <span className="text-xs text-muted-foreground">
                      {parseUtcTimestamp(a.created_at).toLocaleDateString("vi-VN")}
                    </span>
                  </div>
                ))}
                {assignments.length === 0 && (
                  <p className="py-8 text-center text-sm text-muted-foreground">
                    Chưa có phân công nào.
                  </p>
                )}
              </div>
            </section>
            <section className="rounded-2xl border border-border bg-card p-5 lg:col-span-2">
              <h2 className="font-semibold">Thao tác nhanh</h2>
              <div className="mt-4 grid gap-2">
                <button
                  onClick={() => setShowAssign(true)}
                  className="flex items-center gap-3 rounded-xl border border-border p-3 text-left hover:bg-muted"
                >
                  <span className="rounded-lg bg-emerald-50 p-2 text-emerald-600">
                    <Link2 className="h-4 w-4" />
                  </span>
                  <span>
                    <b className="block text-sm">Gán bệnh nhân</b>
                    <small className="text-muted-foreground">
                      Kết nối bác sĩ phụ trách
                    </small>
                  </span>
                </button>
                <button
                  onClick={() => navigateTo("doctors")}
                  className="flex items-center gap-3 rounded-xl border border-border p-3 text-left hover:bg-muted"
                >
                  <span className="rounded-lg bg-blue-50 p-2 text-blue-600">
                    <Stethoscope className="h-4 w-4" />
                  </span>
                  <span>
                    <b className="block text-sm">Danh sách bác sĩ</b>
                    <small className="text-muted-foreground">
                      Xem và quản lý tài khoản
                    </small>
                  </span>
                </button>
                <button
                  onClick={() => navigateTo("audit")}
                  className="flex items-center gap-3 rounded-xl border border-border p-3 text-left hover:bg-muted"
                >
                  <span className="rounded-lg bg-purple-50 p-2 text-purple-600">
                    <ClipboardList className="h-4 w-4" />
                  </span>
                  <span>
                    <b className="block text-sm">Nhật ký hoạt động</b>
                    <small className="text-muted-foreground">
                      Truy vết truy cập, yêu cầu xóa
                    </small>
                  </span>
                </button>
              </div>
            </section>
          </div>
        </div>
      )}

      {!loading && activeTab === "users" && (
        <section className="overflow-hidden rounded-2xl border border-border bg-card">
          <div className="border-b border-border p-5">
            <div className="flex flex-col gap-3 md:flex-row">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                <input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder="Tìm tên hoặc email..."
                  className="w-full rounded-xl border border-border  py-2.5 pl-10 pr-4 text-sm outline-none focus:border-primary"
                />
              </div>
              <ConfigProvider
                theme={{
                  token: {
                    borderRadius: 12,
                    controlHeightLG: 42,
                    fontSize: 14,
                  },
                  components: {
                    Select: {
                      optionSelectedFontWeight: 500,
                      optionSelectedBg: "#39ada7",
                      optionActiveBg: "#d9f0ef",
                      activeBorderColor: "#39ada7",
                    },
                  },
                }}
              >
                <Select
                  value={roleFilter}
                  onChange={(value) => setRoleFilter(value as RoleFilter)}
                  className="w-[190px]"
                  size="large"
                  options={[
                    { value: "all", label: "Tất cả vai trò" },
                    { value: "patient", label: "Bệnh nhân" },
                    { value: "doctor", label: "Bác sĩ" },
                    { value: "admin", label: "Quản trị viên" },
                  ]}
                />
              </ConfigProvider>
              <ConfigProvider
                theme={{
                  token: {
                    borderRadius: 12,
                    controlHeightLG: 42,
                    fontSize: 14,
                  },
                  components: {
                    Select: {
                      optionSelectedFontWeight: 500,
                      optionSelectedBg: "#39ada7",
                      optionActiveBg: "#d9f0ef",
                      activeBorderColor: "#39ada7",
                    },
                  },
                }}
              >
                <Select
                  value={statusFilter}
                  onChange={(value) => setStatusFilter(value as StatusFilter)}
                  className="w-[190px] rounded-xl border border-border px-3 py-2.5 text-sm"
                  size="large"
                  options={[
                    { value: "all", label: "Tất cả trạng thái" },
                    { value: "active", label: "Hoạt động" },
                    { value: "locked", label: "Bị khóa" },
                  ]}
                />
              </ConfigProvider>
            </div>
          </div>
          <div className="divide-y divide-border">
            {paginatedUsers.map((user) => {
              const role = roleConfig[user.role];
              return (
                <div
                  key={user.id}
                  className="flex items-center gap-3 p-4 hover:bg-muted/30"
                >
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-muted">
                    <role.icon className="h-5 w-5 text-muted-foreground" />
                  </div>
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-sm font-semibold">
                      {user.full_name}
                    </p>
                    <p className="truncate text-xs text-muted-foreground">
                      {user.email}
                    </p>
                  </div>
                  <span
                    className={cn(
                      "hidden rounded-full border px-2.5 py-1 text-xs font-medium sm:inline-flex",
                      role.style,
                    )}
                  >
                    {role.label}
                  </span>
                  <span
                    className={cn(
                      "hidden items-center gap-1 text-xs md:flex",
                      user.is_active ? "text-emerald-600" : "text-red-600",
                    )}
                  >
                    {user.is_active ? (
                      <CheckCircle2 className="h-3.5 w-3.5" />
                    ) : (
                      <XCircle className="h-3.5 w-3.5" />
                    )}
                    {user.is_active ? "Hoạt động" : "Bị khóa"}
                  </span>
                  <div className="relative">
                    <button
                      onClick={() =>
                        setOpenMenu(openMenu === user.id ? null : user.id)
                      }
                      className="rounded-lg p-2 text-muted-foreground hover:bg-muted"
                    >
                      <MoreVertical className="h-4 w-4" />
                    </button>
                    {openMenu === user.id && (
                      <div className="absolute right-0 top-10 z-20 w-52 rounded-xl border border-border bg-card p-1.5 shadow-xl">
                        <button
                          onClick={() => {
                            setResetUser(user);
                            setGeneratedPassword("");
                            setOpenMenu(null);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-muted"
                        >
                          <KeyRound className="h-4 w-4" /> Đặt lại mật khẩu
                        </button>
                        <button
                          onClick={() => {
                            staticAction(
                              user.is_active
                                ? "Khóa tài khoản"
                                : "Mở khóa tài khoản",
                            );
                            setOpenMenu(null);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm hover:bg-muted"
                        >
                          <Lock className="h-4 w-4" />{" "}
                          {user.is_active ? "Khóa tài khoản" : "Mở khóa"}
                        </button>
                        <button
                          onClick={() => {
                            staticAction("Xóa tài khoản");
                            setOpenMenu(null);
                          }}
                          className="flex w-full items-center gap-2 rounded-lg px-3 py-2 text-left text-sm text-red-600 hover:bg-red-50"
                        >
                          <Trash2 className="h-4 w-4" /> Xóa tài khoản
                        </button>
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
            {filteredUsers.length === 0 && (
              <p className="p-10 text-center text-sm text-muted-foreground">
                Không tìm thấy tài khoản phù hợp.
              </p>
            )}
          </div>
          <Pagination
            page={userPage}
            totalItems={filteredUsers.length}
            pageSize={PAGE_SIZE}
            onPageChange={setUserPage}
          />
        </section>
      )}

      {!loading && activeTab === "assignments" && (
        <section className="rounded-2xl border border-border bg-card">
          <div className="flex items-center justify-between border-b border-border p-5">
            <div>
              <h2 className="font-semibold">Phân công bệnh nhân – bác sĩ</h2>
              <p className="mt-1 text-xs text-muted-foreground">
                Mỗi bệnh nhân có thể được theo dõi bởi bác sĩ phụ trách.
              </p>
            </div>
            <button
              onClick={() => setShowAssign(true)}
              className="flex items-center gap-2 rounded-xl bg-primary px-4 py-2.5 text-sm font-semibold text-primary-foreground"
            >
              <Plus className="h-4 w-4" /> Tạo phân công
            </button>
          </div>
          <div className="space-y-4 m-2">
            {/* Search */}
            <div className="mt-2 flex flex-col gap-2 md:flex-row">
              <div className="relative flex-1">
                <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />

                <input
                  type="text"
                  value={searchTerm}
                  onChange={(e) => setSearchTerm(e.target.value)}
                  placeholder="Tìm bệnh nhân, email hoặc bác sĩ..."
                  className="w-full rounded-xl border border-border bg-background py-2.5 pl-10 pr-4 text-sm outline-none transition-colors placeholder:text-muted-foreground focus:border-primary"
                />
              </div>
              <select
                value={assignmentDoctorFilter}
                onChange={(e) => setAssignmentDoctorFilter(e.target.value)}
                aria-label="Lọc theo bác sĩ"
                className="rounded-xl border border-border bg-background px-4 py-2.5 text-sm outline-none focus:border-primary md:w-64"
              >
                <option value="all">Tất cả bác sĩ</option>
                {doctors.map((doctor) => (
                  <option key={doctor.id} value={doctor.id}>
                    BS. {doctor.full_name}
                  </option>
                ))}
              </select>
            </div>

            {/* Table */}
            <div className="overflow-x-auto rounded-xl border border-border">
              <table className="w-full text-sm">
                <thead className="bg-muted/50">
                  <tr className="border-b border-border">
                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Bệnh nhân
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Email
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Bác sĩ phụ trách
                    </th>

                    <th className="px-4 py-3 text-left font-medium text-muted-foreground">
                      Ngày phân công
                    </th>

                    <th className="px-4 py-3 text-center font-medium text-muted-foreground">
                      Thao tác
                    </th>
                  </tr>
                </thead>

                <tbody>
                  {filteredAssignments.length > 0 ? (
                    paginatedAssignments.map((a) => {
                      const patient = userById.get(a.patient_id);
                      const doctor = userById.get(a.doctor_id);

                      return (
                        <tr
                          key={a.id}
                          className="border-b border-border transition-colors last:border-b-0 hover:bg-muted/30"
                        >
                          {/* Bệnh nhân */}
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-3">
                              <div className="rounded-lg bg-blue-50 p-2 text-blue-600">
                                <Users className="h-4 w-4" />
                              </div>

                              <span className="font-medium">
                                {patient?.full_name ||
                                  "Không tìm thấy bệnh nhân"}
                              </span>
                            </div>
                          </td>

                          {/* Email */}
                          <td className="px-4 py-3 text-muted-foreground">
                            {patient?.email || "—"}
                          </td>

                          {/* Bác sĩ */}
                          <td className="px-4 py-3">
                            <span className="font-medium">
                              BS. {doctor?.full_name || "Không tìm thấy bác sĩ"}
                            </span>
                          </td>

                          {/* Ngày phân công */}
                          <td className="px-4 py-3 text-muted-foreground">
                            {parseUtcTimestamp(a.created_at).toLocaleDateString("vi-VN")}
                          </td>

                          {/* Thao tác */}
                          <td className="px-4 py-3 text-center">
                            <button
                              onClick={() => unassign(a)}
                              title="Bỏ phân công"
                              className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-red-50 hover:text-red-600"
                            >
                              <Unlink className="h-4 w-4" />
                            </button>
                          </td>
                        </tr>
                      );
                    })
                  ) : (
                    <tr>
                      <td
                        colSpan={5}
                        className="px-4 py-10 text-center text-sm text-muted-foreground"
                      >
                        Không tìm thấy phân công phù hợp.
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
            <Pagination
              page={assignmentPage}
              totalItems={filteredAssignments.length}
              pageSize={PAGE_SIZE}
              onPageChange={setAssignmentPage}
              className="px-2"
            />
          </div>
        </section>
      )}

      {!loading && activeTab === "audit" && (
        <div className="space-y-5">
          <AdminDeletionRequests />
          <AdminAuditPanel />
        </div>
      )}

      {!loading && activeTab === "settings" && (
        <section className="rounded-2xl border border-border bg-card">
          <div className="border-b border-border p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="font-semibold">Cấu hình vận hành</h2>
                <p className="mt-1 text-xs text-muted-foreground">
                  Thiết lập giao diện cục bộ, chưa lưu lên backend.
                </p>
              </div>
              <span className="rounded-full bg-orange-50 px-3 py-1 text-xs font-medium text-orange-700">
                Dữ liệu tĩnh
              </span>
            </div>
          </div>
          <div className="divide-y divide-border">
            {[
              [
                "emailAlerts",
                "Thông báo qua email",
                "Gửi tổng hợp hoạt động và cảnh báo đến quản trị viên",
              ],
              [
                "criticalAlerts",
                "Cảnh báo chỉ số nguy hiểm",
                "Ưu tiên thông báo khi bệnh nhân có chỉ số bất thường",
              ],
              [
                "autoBackup",
                "Sao lưu tự động",
                "Tạo bản sao dữ liệu hệ thống mỗi ngày",
              ],
              [
                "maintenance",
                "Chế độ bảo trì",
                "Tạm ngừng truy cập của người dùng thông thường",
              ],
            ].map(([key, label, description]) => (
              <label
                key={key}
                className="flex cursor-pointer items-center gap-4 p-5"
              >
                <div className="flex-1">
                  <p className="text-sm font-semibold">{label}</p>
                  <p className="text-xs text-muted-foreground">{description}</p>
                </div>
                <button
                  type="button"
                  onClick={() =>
                    setSettings((current) => ({
                      ...current,
                      [key]: !current[key as keyof typeof current],
                    }))
                  }
                  className={cn(
                    "relative h-6 w-11 rounded-full transition-colors",
                    settings[key as keyof typeof settings]
                      ? "bg-primary"
                      : "bg-slate-300",
                  )}
                >
                  <span
                    className={cn(
                      "absolute top-1 h-4 w-4 rounded-full bg-white transition-all",
                      settings[key as keyof typeof settings]
                        ? "left-6"
                        : "left-1",
                    )}
                  />
                </button>
              </label>
            ))}
          </div>
          <div className="flex justify-end border-t border-border p-5">
            <button
              onClick={() => staticAction("Lưu cấu hình")}
              className="rounded-xl bg-primary px-5 py-2.5 text-sm font-semibold text-primary-foreground"
            >
              Lưu cấu hình
            </button>
          </div>
        </section>
      )}

      {showAddUser && (
        <Modal
          title="Tạo tài khoản mới"
          onClose={() => {
            setShowNewUserPassword(false);
            setShowAddUser(false);
          }}
        >
          <form onSubmit={createUser} className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-semibold">
                Họ và tên
              </label>
              <input
                required
                value={newUser.full_name}
                onChange={(e) =>
                  setNewUser((v) => ({ ...v, full_name: e.target.value }))
                }
                className="w-full rounded-xl border border-border bg-muted px-4 py-3 text-sm outline-none focus:border-primary"
                placeholder="Nguyễn Văn An"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold">
                Email
              </label>
              <input
                required
                type="email"
                value={newUser.email}
                onChange={(e) =>
                  setNewUser((v) => ({ ...v, email: e.target.value }))
                }
                className="w-full rounded-xl border border-border bg-muted px-4 py-3 text-sm outline-none focus:border-primary"
                placeholder="user@example.com"
              />
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold">
                Mật khẩu tạm
              </label>
              <div className="relative">
                <input
                  required
                  minLength={8}
                  type={showNewUserPassword ? "text" : "password"}
                  value={newUser.temporary_password}
                  onChange={(e) =>
                    setNewUser((v) => ({
                      ...v,
                      temporary_password: e.target.value,
                    }))
                  }
                  className="w-full rounded-xl border border-border bg-muted py-3 pl-4 pr-11 text-sm outline-none focus:border-primary"
                  placeholder="Tối thiểu 8 ký tự"
                />
                <button
                  type="button"
                  onClick={() => setShowNewUserPassword((visible) => !visible)}
                  aria-label={
                    showNewUserPassword ? "Ẩn mật khẩu" : "Hiện mật khẩu"
                  }
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground hover:text-foreground"
                >
                  {showNewUserPassword ? (
                    <EyeOff className="h-4 w-4" />
                  ) : (
                    <Eye className="h-4 w-4" />
                  )}
                </button>
              </div>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold">
                Vai trò
              </label>
              <select
                value={newUser.role}
                onChange={(e) =>
                  setNewUser((v) => ({
                    ...v,
                    role: e.target.value as UserRole,
                  }))
                }
                className="w-full rounded-xl border border-border bg-muted px-4 py-3 text-sm"
              >
                <option value="patient">Bệnh nhân</option>
                <option value="doctor">Bác sĩ</option>
                <option value="admin">Quản trị viên</option>
              </select>
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={() => setShowAddUser(false)}
                className="flex-1 rounded-xl border border-border py-3 text-sm font-medium"
              >
                Hủy
              </button>
              <button
                disabled={saving}
                className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground disabled:opacity-60"
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" />} Tạo tài
                khoản
              </button>
            </div>
          </form>
        </Modal>
      )}

      {showAssign && (
        <Modal
          title="Gán bệnh nhân cho bác sĩ"
          onClose={() => setShowAssign(false)}
        >
          <form onSubmit={assignPatient} className="space-y-4">
            <div>
              <label className="mb-1.5 block text-xs font-semibold">
                Bác sĩ phụ trách
              </label>
              <select
                required
                value={assignmentForm.doctor_id}
                onChange={(e) =>
                  setAssignmentForm((v) => ({
                    ...v,
                    doctor_id: e.target.value,
                  }))
                }
                className="w-full rounded-xl border border-border bg-muted px-4 py-3 text-sm"
              >
                <option value="">Chọn bác sĩ</option>
                {doctors.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.full_name} — {d.email}
                  </option>
                ))}
              </select>
            </div>
            <div>
              <label className="mb-1.5 block text-xs font-semibold">
                Bệnh nhân
              </label>
              <select
                required
                disabled={unassignedPatients.length === 0}
                value={assignmentForm.patient_id}
                onChange={(e) =>
                  setAssignmentForm((v) => ({
                    ...v,
                    patient_id: e.target.value,
                  }))
                }
                className="w-full rounded-xl border border-border bg-muted px-4 py-3 text-sm"
              >
                <option value="">
                  {unassignedPatients.length > 0
                    ? "Chọn bệnh nhân"
                    : "Không còn bệnh nhân chưa phân công"}
                </option>
                {unassignedPatients.map((patient) => (
                  <option key={patient.id} value={patient.id}>
                    {patient.full_name} — {patient.email}
                  </option>
                ))}
              </select>
            </div>
            <div className="rounded-xl bg-blue-50 p-3 text-xs leading-relaxed text-blue-700">
              Sau khi gán, bác sĩ có thể xem dữ liệu sức khỏe và lịch hẹn của
              bệnh nhân này.
            </div>
            <div className="flex gap-3 pt-2">
              <button
                type="button"
                onClick={() => setShowAssign(false)}
                className="flex-1 rounded-xl border border-border py-3 text-sm font-medium"
              >
                Hủy
              </button>
              <button
                disabled={
                  saving ||
                  !assignmentForm.doctor_id ||
                  !assignmentForm.patient_id
                }
                className="flex flex-1 items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground disabled:opacity-50"
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" />} Xác
                nhận gán
              </button>
            </div>
          </form>
        </Modal>
      )}

      {resetUser && (
        <Modal
          title="Đặt lại mật khẩu"
          onClose={() => {
            setResetUser(null);
            setGeneratedPassword("");
          }}
        >
          {generatedPassword ? (
            <div className="space-y-4">
              <div className="rounded-xl border border-emerald-200 bg-emerald-50 p-4">
                <p className="text-xs font-medium text-emerald-700">
                  Mật khẩu tạm mới
                </p>
                <p className="mt-2 break-all font-mono text-lg font-bold text-emerald-900">
                  {generatedPassword}
                </p>
              </div>
              <p className="text-xs text-muted-foreground">
                Hãy chuyển mật khẩu này cho người dùng qua kênh an toàn. Mật
                khẩu sẽ không được hiển thị lại.
              </p>
              <button
                onClick={() => {
                  navigator.clipboard?.writeText(generatedPassword);
                  showToast("Đã sao chép mật khẩu.", "success");
                }}
                className="w-full rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground"
              >
                Sao chép mật khẩu
              </button>
            </div>
          ) : (
            <form onSubmit={handleResetPassword} className="space-y-4">
              <div className="rounded-xl bg-muted p-3">
                <p className="text-sm font-semibold">{resetUser.full_name}</p>
                <p className="text-xs text-muted-foreground">
                  {resetUser.email}
                </p>
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-semibold">
                  Mật khẩu tạm mới (không bắt buộc)
                </label>
                <input
                  minLength={8}
                  value={resetPassword}
                  onChange={(e) => setResetPassword(e.target.value)}
                  type="password"
                  placeholder="Để trống để hệ thống tự tạo"
                  className="w-full rounded-xl border border-border bg-muted px-4 py-3 text-sm outline-none focus:border-primary"
                />
              </div>
              <p className="text-xs text-muted-foreground">
                Các phiên đăng nhập cũ của tài khoản sẽ bị vô hiệu hóa.
              </p>
              <button
                disabled={
                  saving || (!!resetPassword && resetPassword.length < 8)
                }
                className="flex w-full items-center justify-center gap-2 rounded-xl bg-primary py-3 text-sm font-semibold text-primary-foreground disabled:opacity-50"
              >
                {saving && <Loader2 className="h-4 w-4 animate-spin" />} Đặt lại
                mật khẩu
              </button>
            </form>
          )}
        </Modal>
      )}
    </div>
  );
}
