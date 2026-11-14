import { SignUp } from "@clerk/nextjs";

export const metadata = { title: "Criar conta" };

// Private preview: restrict sign-ups to invited people in the Clerk dashboard. Even a created
// account only gets in when the API finds an invitation for its verified e-mail.
export default function SignUpPage() {
  return (
    <div className="centered">
      <SignUp />
    </div>
  );
}
