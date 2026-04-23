/**
 * Tiny TypeScript fixture to exercise multi-language indexing.
 */

export interface User {
  id: string;
  name: string;
}

export class UserService {
  private users: Map<string, User> = new Map();

  addUser(user: User): void {
    this.users.set(user.id, user);
  }

  findByName(name: string): User | undefined {
    for (const u of this.users.values()) {
      if (u.name === name) return u;
    }
    return undefined;
  }
}

export function createService(): UserService {
  return new UserService();
}
