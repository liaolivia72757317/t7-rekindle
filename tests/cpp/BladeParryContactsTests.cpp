#include "../../src/Runtime/launcher/BladeParryContacts.h"
#include <iostream>
#include <stdexcept>

namespace {
t7::WorldSnapshot snapshot(uint64_t at, float offset) {
    t7::WorldSnapshot world;
    world.ready=world.contactsReady=true; world.instanceStartedAt=100; world.sampledAt=at;
    auto& source=world.actors[0]; auto& target=world.actors[1];
    source.ready=target.ready=true; source.entity=101; target.entity=102;
    source.state=238; source.sequence=3; source.phaseAt=900;
    target.state=223; target.sequence=2; target.phaseAt=850;
    source.position={0,0,.5f}; target.position={1.2f,0,.5f}; target.heading=180;
    source.blade.poseReady=target.blade.poseReady=true;
    source.blade.base={.5f,offset,1.4f}; source.blade.tip={1.5f,offset,1.4f};
    target.blade.base={1,-.1f,1}; target.blade.tip={1,.1f,1.8f};
    return world;
}
void require(bool value) { if(!value) throw std::runtime_error("guard blade contact invariant failed"); }
}
bool verifyBladeParryContacts() {
    t7::BladeParryContacts contacts;
    auto first=snapshot(1000,-.3f); contacts.append(first);
    require(first.contacts.empty());
    auto second=snapshot(1050,.3f); contacts.append(second);
    require(second.contacts.size()==1);
    const auto& hit=second.contacts[0];
    require(hit.weaponContact && hit.actorId==1 && hit.hit.targetId==2 && hit.entity==101 && hit.targetEntity==102);
    require(hit.occurredAt>1000 && hit.occurredAt<1050 && hit.hit.contact.hit && hit.hit.contact.ready);
    // A gap, new match, new object or a released guard cannot reuse old geometry.
    for(int kind=0;kind<4;++kind) {
        contacts.clear(); first=snapshot(1000,-.3f); contacts.append(first);
        second=snapshot(kind==0 ? 1200 : 1050,.3f);
        if(kind==1) second.instanceStartedAt=200;
        if(kind==2) second.actors[1].entity=202;
        if(kind==3) second.actors[1].state=2;
        contacts.append(second); require(second.contacts.empty());
    }
    contacts.clear(); first=snapshot(1000,0); contacts.append(first);
    require(first.contacts.size()==1); // Initial overlap needs no body collision.
    std::cout << "guard weapon overlap, swept contact and stale pose isolation passed\n";
    return true;
}
