import { SignIn } from "@clerk/nextjs";

export const metadata = { title: "Entrar" };

export default function SignInPage() {
  return (
    <div className="centered">
      <SignIn />
    </div>
  );
}
