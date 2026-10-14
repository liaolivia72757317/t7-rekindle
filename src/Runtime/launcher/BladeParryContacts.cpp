#include "BladeParryContacts.h"
#include <algorithm>
#include <cmath>

namespace t7 {
namespace {
using Point = std::array<float, 3>;
constexpr float CONTACT_DISTANCE = .06f;
Point subtract(const Point& a, const Point& b) {
    return {a[0]-b[0], a[1]-b[1], a[2]-b[2]};
}
float dot(const Point& a, const Point& b) { return a[0]*b[0]+a[1]*b[1]+a[2]*b[2]; }
Point lerp(const Point& a, const Point& b, float t) {
    return {a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t};
}
struct Nearest { Point source, target; float along; };
Nearest nearest(const Point& a, const Point& b, const Point& c, const Point& d) {
    const auto u=subtract(b,a), v=subtract(d,c), w=subtract(a,c);
    const float uu=dot(u,u), vv=dot(v,v), uv=dot(u,v), uw=dot(u,w), vw=dot(v,w);
    const auto denominator=uu*vv-uv*uv;
    float s=denominator>1e-8f ? std::clamp((uv*vw-vv*uw)/denominator,0.f,1.f) : 0.f;
    float t=(uv*s+vw)/vv;
    if(t<0) { t=0; s=std::clamp(-uw/uu,0.f,1.f); }
    else if(t>1) { t=1; s=std::clamp((uv-uw)/uu,0.f,1.f); }
    return {lerp(a,b,s),lerp(c,d,t),s};
}
bool pose(const ActorSnapshot& actor) {
    if(!actor.ready || !actor.entity || !actor.blade.poseReady) return false;
    for(const auto& point : {actor.blade.base,actor.blade.tip})
        for(float coordinate : point)
            if(!std::isfinite(coordinate) || std::abs(coordinate)>=100000) return false;
    const auto segment=subtract(actor.blade.tip,actor.blade.base);
    const auto offset=subtract(actor.blade.base,actor.position);
    return dot(segment,segment)>=.0001f && dot(segment,segment)<=64.f && dot(offset,offset)<=25.f;
}
bool strike(uint32_t state) { return state==238 || state==244 || state==250 || state==256; }
bool guard(uint32_t state) { return state>=223 && state<=226; }
bool continuous(const ActorSnapshot& old, const ActorSnapshot& current) {
    return pose(old) && old.entity==current.entity && old.sequence==current.sequence && old.state==current.state
        && dot(subtract(old.blade.base,current.blade.base),subtract(old.blade.base,current.blade.base))<=16.f
        && dot(subtract(old.blade.tip,current.blade.tip),subtract(old.blade.tip,current.blade.tip))<=16.f;
}
}
void BladeParryContacts::append(WorldSnapshot& world) {
    if(!world.ready || !world.contactsReady || !world.instanceStartedAt) { clear(); return; }
    const bool same=previous_.ready && previous_.instanceStartedAt==world.instanceStartedAt
        && world.sampledAt>previous_.sampledAt && world.sampledAt-previous_.sampledAt<=100;
    for(size_t i=0;i<world.actors.size();++i) {
        const auto& source=world.actors[i];
        if(!pose(source) || !strike(source.state) || !source.sequence) continue;
        for(size_t j=0;j<world.actors.size();++j) {
            const auto& target=world.actors[j];
            if(i==j || !pose(target) || !guard(target.state)) continue;
            const auto gap=subtract(source.position,target.position);
            if(dot(gap,gap)>16.f) continue;
            const bool sweep=same && continuous(previous_.actors[i],source) && continuous(previous_.actors[j],target);
            const auto& from=sweep ? previous_.actors[i] : source;
            const auto& defender=sweep ? previous_.actors[j] : target;
            const unsigned steps=sweep ? 32u : 1u;
            for(unsigned step=sweep ? 0u : 1u;step<=steps;++step) {
                const float time=static_cast<float>(step)/static_cast<float>(steps);
                const auto hit=nearest(lerp(from.blade.base,source.blade.base,time),lerp(from.blade.tip,source.blade.tip,time),
                    lerp(defender.blade.base,target.blade.base,time),lerp(defender.blade.tip,target.blade.tip,time));
                const auto separation=subtract(hit.source,hit.target);
                if(dot(separation,separation)>CONTACT_DISTANCE*CONTACT_DISTANCE) continue;
                BladeContactEvent event;
                event.actorId=static_cast<uint32_t>(i+1); event.entity=source.entity; event.targetEntity=target.entity;
                event.state=source.state; event.sequence=source.sequence; event.phaseAt=source.phaseAt;
                event.targetState=target.state; event.targetSequence=target.sequence; event.targetPhaseAt=target.phaseAt;
                event.instanceStartedAt=world.instanceStartedAt;
                event.occurredAt=sweep ? static_cast<double>(previous_.sampledAt)
                    +static_cast<double>(world.sampledAt-previous_.sampledAt)*time
                    : static_cast<double>(world.sampledAt);
                if(event.occurredAt<source.phaseAt || event.occurredAt<target.phaseAt) continue;
                event.sourcePosition=source.position; event.targetPosition=target.position;
                event.sourceHeading=source.heading; event.targetHeading=target.heading; event.weaponContact=true;
                event.hit.targetId=static_cast<uint32_t>(j+1); event.hit.time=time; event.hit.along=hit.along;
                auto& contact=event.hit.contact;
                contact.ready=contact.hit=true; contact.material=4; contact.position=lerp(hit.source,hit.target,.5f);
                const auto yaw=source.heading*3.14159265358979323846f/180.f;
                contact.direction={std::cos(yaw),-std::sin(yaw),0};
                const auto length=std::sqrt(dot(separation,separation));
                contact.normal=length>.0001f ? Point{separation[0]/length,separation[1]/length,separation[2]/length}
                    : Point{-contact.direction[0],-contact.direction[1],0};
                world.contacts.push_back(event);
                break;
            }
        }
    }
    previous_=world;
    previous_.contacts.clear();
}
}
