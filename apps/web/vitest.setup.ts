import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { createElement, type ReactNode } from "react";
import { afterEach, vi } from "vitest";

import { resetClerk } from "./src/test/clerk-mock";

vi.mock("@clerk/nextjs", () => import("./src/test/clerk-mock"));
vi.mock("next/navigation", () => import("./src/test/navigation-mock"));
vi.mock("next/link", () => ({
  default: ({ href, children, ...rest }: { href: string; children: ReactNode }) =>
    createElement("a", { href, ...rest }, children),
}));

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  resetClerk();
});
