export interface CreateUserDto {
  username: string;
  email: string;
}

export interface User {
  id: string;
  username: string;
  email: string;
}

export async function createUser(dto: CreateUserDto): Promise<User> {
  return {
    id: "u_1",
    username: dto.username,
    email: dto.email
  };
}
