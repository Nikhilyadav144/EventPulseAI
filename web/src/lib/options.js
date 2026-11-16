// Allowed values — mirror the backend data contract (app.Registration / webapp.server.EventIn).
export const EVENT_TYPES = ["Technical", "Workshop", "Cultural", "Seminar", "Sports"];
export const DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"];
export const SLOTS = ["Morning", "Afternoon", "Evening"];
export const WEATHER = ["Clear", "Cloudy", "Rainy", "Unavailable"];
export const COURSES = ["B.Com (Hons)", "B.Com (Hons) Financial Markets", "BCA", "BCA (GD)", "BCA (Media & IT)", "MCA", "B.Tech CSE",
  "B.Tech ECE", "B.Tech Robotics & AI", "BBA LLB (Hons)", "LLB (3-year)", "BA (Hons) Economics", "BA (Hons) Psychology", "MA Psychology",
  "BBA", "BBA Business Analytics", "BBA Digital Marketing"];
export const EXPERIENCE = ["No experience", "Some experience", "Experienced"];
export const INTERESTS = ["Community & Social Work", "Team Activities & Challenges", "Problem Solving & Decision Making", "Awareness & Communication", "Not Sure Yet"];
export const ROLES = ["Cultural & Event Management", "Graphics/Creativity", "Social Media", "Content", "Village Attache", "Multiple teams", "Did not apply"];
export const REG_STATUS = ["Registered", "Waitlisted", "Cancelled"];
export const EVENT_STATUS = ["Scheduled", "Completed", "Cancelled"];
export const dayOf = (iso) => (iso ? DAYS[(new Date(iso + "T12:00:00").getDay() + 6) % 7] : "");
