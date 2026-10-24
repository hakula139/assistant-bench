import { WorkerEntrypoint } from 'cloudflare:workers';

import { payload } from './rpc-payload';

export default {
  fetch(): Response {
    return new Response(null, { status: 404 });
  },
};

interface RenderOwner {
  lookup(capability: string): Promise<string>;
}

interface ManagementOwner {
  command(request: string): Promise<string>;
}

interface RenderEnvironment {
  RENDER_OWNER: RenderOwner;
}

interface ManagementEnvironment {
  MANAGEMENT_OWNER: ManagementOwner;
}

export class RenderLookup extends WorkerEntrypoint<RenderEnvironment> {
  lookup(capability: unknown): Promise<string> {
    return this.env.RENDER_OWNER.lookup(payload(capability, 2048));
  }
}

export class Management extends WorkerEntrypoint<ManagementEnvironment> {
  command(request: unknown): Promise<string> {
    return this.env.MANAGEMENT_OWNER.command(payload(request, 1_048_576));
  }
}
