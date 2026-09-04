import { useUser } from 'src/hooks/useUser';
import { getUserDisplayName, UNKNOWN_USER_LABEL } from 'src/utils/userDisplay';

interface UserDisplayProps {
  /** The stored created_by value — either a UUID user_id or a legacy email string. */
  userId: string;
}

/**
 * Resolves a user_id to a human-readable display name.
 * Falls back through optional profile claims, then a generic label.
 */
function UserDisplay({ userId }: UserDisplayProps) {
  const user = useUser(userId);
  return (
    <>
      {user
        ? getUserDisplayName(user)
        : userId.includes('@')
          ? userId
          : UNKNOWN_USER_LABEL}
    </>
  );
}

export default UserDisplay;
