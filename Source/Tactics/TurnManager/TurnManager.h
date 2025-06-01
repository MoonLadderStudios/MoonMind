#pragma once

#include "CoreMinimal.h"
#include "GameFramework/Actor.h"
#include "TurnManager.generated.h"

UCLASS()
class ATurnManager : public AActor
{
    GENERATED_BODY()

public:
    ATurnManager();

protected:
    virtual void BeginPlay() override;

public:
    virtual void Tick(float DeltaTime) override;
};
