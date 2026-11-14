import { vi } from "vitest";

export const router = { replace: vi.fn(), push: vi.fn(), refresh: vi.fn() };
export const navigation = { pathname: "/dashboard" };

export const useRouter = () => router;
export const usePathname = () => navigation.pathname;
