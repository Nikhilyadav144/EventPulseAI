import { createContext, useContext } from "react";

// Signed-in user + sign-out action, provided by App. Nothing about the session is stored in the
// browser except the HttpOnly cookie, which JavaScript cannot read.
export const AuthContext = createContext({ user: null, signOut: () => {} });
export const useAuth = () => useContext(AuthContext);
